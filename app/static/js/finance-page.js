(function(global) {
    'use strict';

    function setFinanceView(view) {
        const depositSection = document.getElementById('deposit-section');
        const withdrawSection = document.getElementById('withdraw-section');
        const depositToggle = document.getElementById('deposit-toggle');
        const withdrawToggle = document.getElementById('withdraw-toggle');

        if (!depositSection || !withdrawSection || !depositToggle || !withdrawToggle) return;

        const showingDeposit = view !== 'withdraw';
        depositSection.style.display = showingDeposit ? 'block' : 'none';
        withdrawSection.style.display = showingDeposit ? 'none' : 'block';
        depositToggle.classList.toggle('active', showingDeposit);
        withdrawToggle.classList.toggle('active', !showingDeposit);
    }

    function initFinancePage() {
        const root = document.querySelector('.finance-page');
        if (!root || root.dataset.financeReady === '1') return;
        root.dataset.financeReady = '1';

        const depositToggle = document.getElementById('deposit-toggle');
        const withdrawToggle = document.getElementById('withdraw-toggle');

        if (depositToggle) {
            depositToggle.addEventListener('click', () => setFinanceView('deposit'));
        }
        if (withdrawToggle) {
            withdrawToggle.addEventListener('click', () => setFinanceView('withdraw'));
        }

        setFinanceView('deposit');
    }

    global.showDeposit = function() {
        setFinanceView('deposit');
    };

    global.showWithdraw = function() {
        setFinanceView('withdraw');
    };

    document.addEventListener('turbo:load', initFinancePage);
    document.addEventListener('turbo:render', initFinancePage);
    document.addEventListener('DOMContentLoaded', initFinancePage);
})(window);
