"""
Blockchain Service
Handle blockchain transactions and multi-coin deposits
Enhanced with Alchemy RPC support
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from flask import current_app
from web3 import Web3

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import Deposit, User


AMOUNT_QUANT = Decimal('0.000001')


@dataclass
class TransferRecord:
    tx_hash: str
    block_number: int
    amount: Decimal
    coin_type: str


class RPCThrottler:
    """Helper to throttle and retry RPC requests."""
    def __init__(self, rate_limit_per_sec=5):
        self.rate_limit_per_sec = rate_limit_per_sec
        self._last_call = 0.0

    def wait(self):
        elapsed = time.time() - self._last_call
        interval = 1.0 / self.rate_limit_per_sec
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call = time.time()

    def call(self, func, *args, **kwargs):
        retries = 4
        backoff = 0.5
        last_exc = None
        for attempt in range(retries):
            try:
                self.wait()
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                if 'limit' in msg or 'exceed' in msg or 'rate' in msg or 'timeout' in msg:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                else:
                    raise
        # if we reach here, all retries failed
        raise last_exc


class BlockchainService:
    """Service for interacting with BNB Chain blockchain via Alchemy RPC."""

    def __init__(self):
        self.web3 = None
        self.contracts = {}  # coin_type -> contract
        self._initialized = False
        self.transfer_topics = {}  # coin_type -> topic
        self.wallet_topic = None
        self.rpc_throttler = RPCThrottler(rate_limit_per_sec=10)

        self.erc20_abi = [
            {
                'anonymous': False,
                'inputs': [
                    {'indexed': True, 'internalType': 'address', 'name': 'from', 'type': 'address'},
                    {'indexed': True, 'internalType': 'address', 'name': 'to', 'type': 'address'},
                    {'indexed': False, 'internalType': 'uint256', 'name': 'value', 'type': 'uint256'},
                ],
                'name': 'Transfer',
                'type': 'event',
            }
        ]

    def _initialize(self):
        if self._initialized:
            return

        try:
            # Try primary RPC (Alchemy), fallback to backup
            rpc_urls = [
                current_app.config.get('BSC_RPC'),
                current_app.config.get('BSC_RPC_FALLBACK', 'https://bsc-dataseed.binance.org/')
            ]
            
            self.web3 = None
            for rpc_url in rpc_urls:
                if not rpc_url:
                    continue
                try:
                    self.web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 15}))
                    if self.web3.is_connected():
                        current_app.logger.info(f'Connected to BSC via {rpc_url.split("//")[1].split("/")[0]}')
                        break
                except Exception:
                    continue
            
            if not self.web3 or not self.web3.is_connected():
                current_app.logger.error('Failed to connect to any BSC RPC endpoint')
                return

            # sanity: ensure chain id matches expected (56 for BSC mainnet)
            try:
                chain_id = self.web3.eth.chain_id
                current_app.logger.info(f'RPC chain id={chain_id}')
                if chain_id not in (56, 97):
                    current_app.logger.warning(f'Connected to unexpected chain id {chain_id}')
            except Exception:
                pass

            wallet_address = current_app.config.get('WALLET_ADDRESS', '')
            coin_contracts = current_app.config.get('COIN_CONTRACTS', {})

            # Initialize contract references for each coin
            for coin_type, config in coin_contracts.items():
                if config.get('address'):  # BNB has no contract (None)
                    try:
                        contract_address = self.web3.to_checksum_address(config['address'])
                        self.contracts[coin_type] = self.web3.eth.contract(
                            address=contract_address,
                            abi=self.erc20_abi
                        )
                        topic = self.web3.keccak(text='Transfer(address,address,uint256)').hex()
                        self.transfer_topics[coin_type] = topic
                    except Exception as e:
                        current_app.logger.error(f'Failed to init {coin_type} contract: {e}')

            # Setup wallet topic for all coins
            if wallet_address:
                wallet = wallet_address.lower().replace('0x', '')
                self.wallet_topic = '0x' + ('0' * 24) + wallet

            self._initialized = True
        except Exception as exc:
            current_app.logger.error(f'Blockchain init failed: {exc}')

    def is_available(self):
        if not self._initialized:
            self._initialize()
        return self.web3 is not None and self.web3.is_connected()

    def _validate_block_range(self, start_block: int, end_block: int) -> bool:
        """Ensure the block window is sane before making RPC calls.

        Guard against negative numbers, reversed ranges or wildly outdated values
        (e.g. 1 to 2k on a chain where current block is millions).  Logs are emitted
        when the range is rejected so operators can adjust configuration.
        """
        if start_block is None or end_block is None:
            current_app.logger.error(f'Invalid null block range {start_block}-{end_block}')
            return False
        if start_block < 0 or end_block < 0:
            current_app.logger.error(f'Negative block numbers {start_block}-{end_block}')
            return False
        if start_block > end_block:
            current_app.logger.error(f'Reversed block range {start_block}-{end_block}')
            return False
        # optional sanity: don't scan ranges that end far in the past (>30M blocks behind on BSC)
        try:
            current = self.get_current_block()
            if current is not None and end_block < current - 30_000_000:
                current_app.logger.warning(f'Block range {start_block}-{end_block} is too far behind current {current}, skipping')
                return False
        except Exception:
            pass
        return True

    def get_current_block(self) -> int | None:
        if not self.is_available():
            return None
        try:
            return int(self.web3.eth.block_number)
        except Exception:
            return None

    def _decode_transfer_amount(self, log_entry, coin_type: str) -> Decimal:
        """Decode transfer amount from log data."""
        data = log_entry.get('data')

        if hasattr(data, 'hex'):
            data_hex = data.hex()
        else:
            data_hex = str(data)

        if data_hex.startswith('0x'):
            data_hex = data_hex[2:]

        raw_value = int(data_hex or '0', 16)
        
        coin_contracts = current_app.config.get('COIN_CONTRACTS', {})
        contract_config = coin_contracts.get(coin_type, {})
        decimals = contract_config.get('decimals', 18)
        
        return (Decimal(raw_value) / (Decimal(10) ** decimals)).quantize(AMOUNT_QUANT)

    def _fetch_transfers(self, coin_type: str, start_block: int, end_block: int) -> list[TransferRecord]:
        """Fetch incoming transfers to the configured wallet for a coin in a block window."""
        wallet_address = current_app.config.get('WALLET_ADDRESS', '').lower()
        transfers: list[TransferRecord] = []

        # sanity-check the window before doing any RPC work
        if not self._validate_block_range(start_block, end_block):
            return []

        if coin_type == 'BNB':
            # scan each block for native transfers to wallet
            for blk in range(start_block, end_block + 1):
                try:
                    block = self.rpc_throttler.call(self.web3.eth.get_block, blk, True)
                except Exception as exc:
                    current_app.logger.error(f'Error fetching block {blk}: {exc}')
                    continue
                if not block or 'transactions' not in block:
                    continue
                for tx in block['transactions']:
                    to = tx.get('to') or ''
                    if to and to.lower() == wallet_address:
                        amount = Decimal(tx.get('value', 0)) / (Decimal(10) ** 18)
                        transfers.append(TransferRecord(tx_hash=tx.hash.hex(), block_number=blk, amount=amount, coin_type='BNB'))
        else:
            contract = self.contracts.get(coin_type)
            if not contract:
                return []
            topic = self.transfer_topics.get(coin_type)
            if not topic:
                return []
            filter_params = {
                'address': contract.address,
                'fromBlock': start_block,
                'toBlock': end_block,
                'topics': [topic, None, self.wallet_topic],
            }
            try:
                logs = self.rpc_throttler.call(self.web3.eth.get_logs, filter_params)
            except Exception as exc:
                current_app.logger.error(f'Error fetching logs {start_block}-{end_block} {coin_type}: {exc}')
                return []
            for entry in logs:
                try:
                    tx_hash = entry['transactionHash'].hex() if hasattr(entry['transactionHash'], 'hex') else str(entry['transactionHash'])
                    block_number = int(entry['blockNumber'])
                    amount = self._decode_transfer_amount(entry, coin_type)
                    transfers.append(TransferRecord(tx_hash=tx_hash, block_number=block_number, amount=amount, coin_type=coin_type))
                except Exception:
                    continue
        return transfers

    def _match_transfer(self, transfer: TransferRecord, current_block: int):
        """Match a detected transfer with a pending deposit and credit the user."""
        # avoid duplicates
        if Deposit.query.filter_by(tx_hash=transfer.tx_hash).first():
            return

        # find matching deposit: same coin and expected amount
        deposit = Deposit.query.filter(
            Deposit.status == 'pending',
            Deposit.coin_type == transfer.coin_type,
            (Deposit.expected_amount == transfer.amount) | (Deposit.usdt_amount == transfer.amount)
        ).order_by(Deposit.created_at.asc()).first()

        if not deposit:
            return

        now = utc_now()
        if deposit.expires_at and now > deposit.expires_at:
            return

        user = User.query.get(deposit.user_id)
        if not user:
            return

        coin_config = current_app.config.get('COIN_CONTRACTS', {}).get(transfer.coin_type, {})
        to_points = coin_config.get('to_points', 4000)
        points = int((Decimal(str(deposit.usdt_amount)) * Decimal(to_points)).to_integral_value(rounding=ROUND_DOWN))

        deposit.status = 'success'
        deposit.blockchain_status = 'verified'
        deposit.tx_hash = transfer.tx_hash
        deposit.tx_block_number = transfer.block_number
        deposit.confirmations = current_block - transfer.block_number + 1
        deposit.verified_at = now
        deposit.paid_at = now
        deposit.credited_at = now
        deposit.coins_added = points

        user.coins = int(user.coins or 0) + points
        db.session.add(deposit)
        db.session.add(user)
        db.session.commit()

    def scan_for_deposits(self):
        """Efficiently scan new blocks and match transfers."""
        if not self.is_available():
            return

        # expire overdue deposits first
        from app.services.deposit_service import DepositService
        DepositService.expire_overdue_deposits()

        current_block = self.get_current_block()
        if current_block is None:
            return
        required_conf = int(current_app.config.get('DEPOSIT_CONFIRMATIONS', 3))
        target_block = current_block - required_conf
        if target_block < 0:
            return

        coin_contracts = current_app.config.get('COIN_CONTRACTS', {})
        chunk = int(current_app.config.get('BLOCK_SCAN_CHUNK', 10))

        for coin_type in coin_contracts.keys():
            state = None
            try:
                from app.models import BlockchainState
                state = BlockchainState.get_or_create(coin_type)
            except Exception:
                continue

            # if the state is uninitialized or absurdly low, jump to recent blocks
            if state.last_block is None or state.last_block < 10_000_000:
                state.last_block = max(0, target_block - 1000)  # scan last 1000 blocks on init
                db.session.add(state)
                db.session.commit()
                current_app.logger.info(f'Initialized {coin_type} state.last_block to {state.last_block}')

            start = int(state.last_block or 0) + 1
            if start > target_block:
                continue

            while start <= target_block:
                end = min(start + chunk - 1, target_block)
                if not self._validate_block_range(start, end):
                    # skip if range looks bogus
                    start = end + 1
                    continue
                transfers = self._fetch_transfers(coin_type, start, end)
                for t in transfers:
                    self._match_transfer(t, current_block)
                start = end + 1

            state.last_block = target_block
            db.session.add(state)
        db.session.commit()

    def get_transfer_logs_to_wallet(self, coin_type: str, from_block: int, to_block: int) -> list[TransferRecord]:
        """Fetch BEP20 transfer logs sent to the receiving wallet for a specific coin."""
        if not self.is_available():
            return []
        if not self._validate_block_range(from_block, to_block):
            return []
        if from_block > to_block:
            return []

        coin_contracts = current_app.config.get('COIN_CONTRACTS', {})
        if coin_type not in coin_contracts:
            return []

        # BNB is native and doesn't have transfer events
        if coin_type == 'BNB':
            return []

        base_chunk_size = int(current_app.config.get('DEPOSIT_LOG_CHUNK_SIZE', 1200))
        min_chunk_size = int(current_app.config.get('DEPOSIT_LOG_MIN_CHUNK_SIZE', 25))
        chunk_size = max(min_chunk_size, base_chunk_size)
        all_logs = []

        start = int(from_block)
        end_target = int(to_block)
        contract_config = coin_contracts[coin_type]
        contract_address = contract_config.get('address')

        if not contract_address:
            return []

        transfer_topic = self.transfer_topics.get(coin_type)
        if not transfer_topic:
            return []

        while start <= end_target:
            end = min(start + chunk_size - 1, end_target)
            try:
                filter_params = {
                    'address': contract_address,
                    'fromBlock': start,
                    'toBlock': end,
                    'topics': [transfer_topic, None, self.wallet_topic],
                }
                logs = self.web3.eth.get_logs(filter_params)
                all_logs.extend(logs)
                start = end + 1
                # Recover chunk size after successful calls
                if chunk_size < base_chunk_size:
                    chunk_size = min(base_chunk_size, chunk_size * 2)
            except Exception as exc:
                msg = str(exc).lower()
                is_rate_limited = ('-32005' in msg) or ('limit exceeded' in msg) or ('response size exceeded' in msg)
                if is_rate_limited and chunk_size > min_chunk_size:
                    new_chunk = max(min_chunk_size, chunk_size // 2)
                    current_app.logger.warning(
                        f'RPC limit for {coin_type} logs {start}-{end}; reducing chunk {chunk_size} -> {new_chunk}'
                    )
                    chunk_size = new_chunk
                    continue
                current_app.logger.error(f'Failed to load {coin_type} logs {start}-{end}: {exc}')
                start = end + 1

        transfers = []
        for entry in all_logs:
            try:
                tx_hash = entry['transactionHash'].hex() if hasattr(entry['transactionHash'], 'hex') else str(entry['transactionHash'])
                block_number = int(entry['blockNumber'])
                amount = self._decode_transfer_amount(entry, coin_type)
                transfers.append(TransferRecord(tx_hash=tx_hash, block_number=block_number, amount=amount, coin_type=coin_type))
            except Exception:
                continue

        transfers.sort(key=lambda item: (item.block_number, item.tx_hash))
        return transfers

    def get_block_timestamp(self, block_number: int, cache: dict[int, datetime]) -> datetime | None:
        """Get timestamp for a block."""
        if block_number in cache:
            return cache[block_number]

        try:
            block = self.web3.eth.get_block(block_number)
            ts = datetime.utcfromtimestamp(int(block['timestamp']))
            cache[block_number] = ts
            return ts
        except Exception:
            return None


class BlockchainChecker:
    """Background thread for checking blockchain deposits."""

    def __init__(self, app):
        self.app = app
        self.thread = None
        self.running = False
        self.service = None
        self._lock = threading.Lock()

    def start(self):
        """Start the blockchain checker thread."""
        if self.running:
            return

        self.running = True
        self.service = BlockchainService()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.app.logger.info('Blockchain checker started')

    def stop(self):
        """Stop the blockchain checker thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        time.sleep(2)
        while self.running:
            try:
                with self.app.app_context():
                    self._check_pending_deposits()
            except Exception as exc:
                self.app.logger.error(f'Error in blockchain checker: {exc}')

            interval = int(self.app.config.get('DEPOSIT_SCAN_INTERVAL', 5))
            time.sleep(max(2, interval))

    @staticmethod
    def _normalize_amount(value) -> Decimal:
        return Decimal(str(value)).quantize(AMOUNT_QUANT)

    def _check_pending_deposits(self):
        if not self._lock.acquire(blocking=False):
            return

        try:
            now = utc_now()
            timeout_seconds = int(self.app.config.get('DEPOSIT_TIMEOUT', 1200))
            required_confirmations = int(self.app.config.get('DEPOSIT_CONFIRMATIONS', 3))
            lookback_blocks = int(self.app.config.get('DEPOSIT_LOOKBACK_BLOCKS', 600))

            pending_deposits = Deposit.query.filter_by(status='pending')\
                .order_by(Deposit.created_at.asc())\
                .limit(5000)\
                .all()

            if not pending_deposits:
                return

            active_deposits = []
            dirty = False

            for deposit in pending_deposits:
                if not deposit.expires_at and deposit.created_at:
                    deposit.expires_at = deposit.created_at + timedelta(seconds=timeout_seconds)
                    dirty = True

                if deposit.expires_at and now >= deposit.expires_at:
                    deposit.status = 'expired'
                    deposit.blockchain_status = 'expired'
                    deposit.last_check = now
                    dirty = True
                    continue

                deposit.last_check = now
                active_deposits.append(deposit)

            if dirty:
                db.session.commit()

            if not active_deposits or not self.service or not self.service.is_available():
                db.session.commit()
                return

            current_block = self.service.get_current_block()
            if current_block is None:
                db.session.commit()
                return

            finalized_block = current_block - required_confirmations + 1
            if finalized_block < 0:
                db.session.commit()
                return

            default_start = max(0, finalized_block - lookback_blocks)
            
            # Group deposits by coin type
            deposits_by_coin = {}
            for deposit in active_deposits:
                coin_type = deposit.coin_type or 'USDT'
                if coin_type not in deposits_by_coin:
                    deposits_by_coin[coin_type] = {'deposits': [], 'by_amount': {}}
                
                deposits_by_coin[coin_type]['deposits'].append(deposit)
                
                expected = deposit.expected_amount if deposit.expected_amount is not None else deposit.usdt_amount
                amount_key = self._normalize_amount(expected)
                deposits_by_coin[coin_type]['by_amount'].setdefault(amount_key, []).append(deposit)
                
                if deposit.scan_from_block is None:
                    deposit.scan_from_block = default_start

            # Check each coin type
            all_transfers = []
            for coin_type, coin_data in deposits_by_coin.items():
                deposits_ = coin_data['deposits']
                scan_starts = []
                
                for deposit in deposits_:
                    next_scan = int(deposit.last_scanned_block + 1) if deposit.last_scanned_block is not None else int(deposit.scan_from_block or 0)
                    if next_scan <= finalized_block:
                        scan_starts.append(next_scan)
                
                if scan_starts:
                    start_block = min(scan_starts)
                    transfers = self.service.get_transfer_logs_to_wallet(coin_type, start_block, finalized_block)
                    all_transfers.extend(transfers)

            if not all_transfers:
                for deposit in active_deposits:
                    if deposit.status == 'pending':
                        deposit.last_scanned_block = finalized_block
                db.session.commit()
                return

            tx_hashes = [item.tx_hash for item in all_transfers]
            existing_hashes = {
                row[0] for row in db.session.query(Deposit.tx_hash)
                .filter(Deposit.tx_hash.in_(tx_hashes))
                .all() if row[0]
            }

            block_time_cache = {}
            claimed_deposit_ids = set()
            coin_contracts = self.app.config.get('COIN_CONTRACTS', {})

            for transfer in all_transfers:
                if transfer.tx_hash in existing_hashes:
                    continue

                amount_key = self._normalize_amount(transfer.amount)
                coin_type = transfer.coin_type
                candidates = deposits_by_coin.get(coin_type, {}).get('by_amount', {}).get(amount_key, [])
                
                if not candidates:
                    continue

                tx_time = self.service.get_block_timestamp(transfer.block_number, block_time_cache)

                for deposit in candidates:
                    if deposit.id in claimed_deposit_ids or deposit.status != 'pending':
                        continue

                    if deposit.created_at and tx_time and tx_time < deposit.created_at:
                        continue

                    if deposit.expires_at and tx_time and tx_time > deposit.expires_at:
                        continue

                    user = User.query.get(deposit.user_id)
                    if not user:
                        break

                    coin_config = coin_contracts.get(deposit.coin_type or 'USDT', {})
                    to_points = coin_config.get('to_points', 4000)
                    points = int(
                        deposit.points_amount
                        if deposit.points_amount is not None
                        else (Decimal(str(deposit.usdt_amount)) * Decimal(to_points)).to_integral_value(rounding=ROUND_DOWN)
                    )

                    deposit.status = 'success'
                    deposit.blockchain_status = 'verified'
                    deposit.tx_hash = transfer.tx_hash
                    deposit.tx_block_number = transfer.block_number
                    deposit.confirmations = current_block - transfer.block_number + 1
                    deposit.verified_at = now
                    deposit.paid_at = tx_time or now
                    deposit.credited_at = now
                    deposit.coins_added = points
                    deposit.last_scanned_block = finalized_block
                    deposit.last_check = now

                    user.coins = int(user.coins or 0) + points

                    existing_hashes.add(transfer.tx_hash)
                    claimed_deposit_ids.add(deposit.id)
                    break

            for deposit in active_deposits:
                if deposit.status == 'pending':
                    deposit.last_scanned_block = finalized_block

            db.session.commit()

        except Exception as exc:
            self.app.logger.error(f'Blockchain processing error: {exc}')
            db.session.rollback()
        finally:
            self._lock.release()


