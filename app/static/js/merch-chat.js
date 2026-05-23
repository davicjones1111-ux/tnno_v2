(function() {
    let chatPollTimer = null;
    let activeConversationId = 0;

    function clearChatPolling() {
        if (chatPollTimer) {
            window.clearInterval(chatPollTimer);
            chatPollTimer = null;
        }
        activeConversationId = 0;
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function formatDateLabel(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const today = new Date();
        const yesterday = new Date();
        yesterday.setDate(today.getDate() - 1);
        const isSameDay = (a, b) =>
            a.getFullYear() === b.getFullYear()
            && a.getMonth() === b.getMonth()
            && a.getDate() === b.getDate();
        if (isSameDay(date, today)) return 'Today';
        if (isSameDay(date, yesterday)) return 'Yesterday';
        return date.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function normalizeImageUrl(imagePath) {
        if (!imagePath) return '';
        if (/^https?:\/\//i.test(String(imagePath))) {
            return String(imagePath);
        }
        const normalized = String(imagePath).replace(/^\/+/, '').replace(/^uploads\//, '');
        return `/static/uploads/${normalized}`;
    }

    function getAttachmentName(message) {
        if (message.attachment_name) return String(message.attachment_name);
        const imagePath = message.image_path || '';
        if (!imagePath) return 'attachment';
        const cleanPath = String(imagePath).split('?')[0].replace(/\/+$/, '');
        const parts = cleanPath.split('/');
        return parts[parts.length - 1] || 'attachment';
    }

    function buildAttachmentMarkup(message) {
        if (!message.image_path) return '';
        if (message.message_type === 'image') {
            return `<img src="${normalizeImageUrl(message.image_path)}" alt="Chat image" class="chat-image">`;
        }
        if (message.message_type === 'file') {
            return `
                <a href="${normalizeImageUrl(message.image_path)}" class="chat-file-chip" target="_blank" rel="noopener" download>
                    <span class="chat-file-icon">FILE</span>
                    <span class="chat-file-meta">
                        <strong>${escapeHtml(getAttachmentName(message))}</strong>
                        <small>Open attachment</small>
                    </span>
                </a>
            `;
        }
        return '';
    }

    function createPendingId() {
        return `pending-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    }

    function initChatPage() {
        const chatPage = document.querySelector('.chat-page');
        if (!chatPage) {
            clearChatPolling();
            return;
        }

        if (chatPage.dataset.chatInitialized === 'true') {
            return;
        }
        chatPage.dataset.chatInitialized = 'true';

        const conversationId = Number(chatPage.dataset.conversationId || 0);
        if (!conversationId) return;

        clearChatPolling();
        activeConversationId = conversationId;

        const messagesUrl = chatPage.dataset.messagesUrl;
        const sendUrl = chatPage.dataset.sendUrl;
        const typingUrl = chatPage.dataset.typingUrl;
        const currentUserId = Number(chatPage.dataset.currentUserId || 0);
        const initialPage = Number(chatPage.dataset.initialPage || 1);
        const totalPages = Number(chatPage.dataset.totalPages || 1);
        let unreadMarkerId = Number(chatPage.dataset.initialUnreadMarkerId || 0);

        const chatMessages = document.getElementById('chatMessages');
        const loadOlderIndicator = document.getElementById('chatLoadOlder');
        const chatForm = document.getElementById('chatForm');
        const imageInput = document.getElementById('imageInput');
        const fileInput = document.getElementById('fileInput');
        const attachmentPreview = document.getElementById('attachmentPreview');
        const messageInput = document.getElementById('messageInput');
        const liveStatus = document.getElementById('chatLiveStatus');
        const typingIndicator = document.getElementById('chatTypingIndicator');
        const imageLightbox = document.getElementById('chatImageLightbox');
        const imageLightboxImg = document.getElementById('chatImageLightboxImg');
        const imageLightboxClose = document.getElementById('chatImageLightboxClose');
        const sendButton = chatForm ? chatForm.querySelector('.chat-send-btn') : null;

        let latestMessageId = 0;
        let isSending = false;
        let pendingMessages = [];
        let typingTimer = null;
        let typingSent = false;
        let loadedMessages = [];
        let highestLoadedPage = initialPage;
        let totalAvailablePages = totalPages;
        let loadingOlder = false;

        function scrollToBottom(force) {
            if (!chatMessages) return;
            const isNearBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight < 120;
            if (force || isNearBottom) {
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        }

        function buildMessageMarkup(message, extraClass) {
            const ownClass = message.sender_id === currentUserId ? 'is-own' : 'is-other';
            const attachmentHtml = buildAttachmentMarkup(message);
            const textHtml = message.content ? `<p class="chat-text">${escapeHtml(message.content)}</p>` : '';
            const retryHtml = extraClass === 'is-failed'
                ? '<button type="button" class="chat-retry-btn" data-chat-retry="true">Retry</button>'
                : '';
            return `
                <article class="chat-bubble ${ownClass} ${extraClass || ''}" data-message-id="${message.id || ''}" data-pending-id="${message.pending_id || ''}">
                    <div class="chat-bubble-inner">
                        ${attachmentHtml}
                        ${textHtml}
                    </div>
                    <div class="chat-meta">
                        <span>${formatTime(message.created_at)}</span>
                        ${message.meta_label ? `<span class="chat-meta-status">${escapeHtml(message.meta_label)}</span>` : ''}
                        ${retryHtml}
                    </div>
                </article>
            `;
        }

        function sortMessages(messages) {
            return [...messages].sort((a, b) => {
                const aId = Number(a.id || 0);
                const bId = Number(b.id || 0);
                return aId - bId;
            });
        }

        function mergeMessages(messages) {
            const byId = new Map(loadedMessages.map((message) => [Number(message.id), message]));
            messages.forEach((message) => {
                byId.set(Number(message.id), message);
            });
            loadedMessages = sortMessages(Array.from(byId.values()));
        }

        function renderMessages(messages, forceScroll) {
            if (!chatMessages) return;
            const mergedMessages = sortMessages(messages);

            const pendingMarkup = pendingMessages.map((message) => (
                buildMessageMarkup(message, message.failed ? 'is-failed' : 'is-pending')
            )).join('');

            if (!mergedMessages.length && !pendingMarkup) {
                chatMessages.innerHTML = '<div class="chat-empty" id="chatEmptyState"><div class="chat-empty-icon">✉️</div><p>No messages yet. Start the chat.</p></div>';
                latestMessageId = 0;
                return;
            }

            const olderMarkup = loadOlderIndicator ? loadOlderIndicator.outerHTML : '<div class="chat-load-older hidden" id="chatLoadOlder">Loading older messages...</div>';
            let unreadInserted = false;
            let lastDateLabel = '';
            const messageMarkup = mergedMessages.map((message) => {
                const currentDateLabel = formatDateLabel(message.created_at);
                let dateSeparator = '';
                if (currentDateLabel && currentDateLabel !== lastDateLabel) {
                    dateSeparator = `<div class="chat-date-separator">${escapeHtml(currentDateLabel)}</div>`;
                    lastDateLabel = currentDateLabel;
                }
                let separator = '';
                if (!unreadInserted && unreadMarkerId && Number(message.id) === Number(unreadMarkerId)) {
                    separator = '<div class="chat-unread-separator" data-unread-separator="true">New messages</div>';
                    unreadInserted = true;
                }
                return dateSeparator + separator + buildMessageMarkup(message, '');
            }).join('');
            chatMessages.innerHTML = olderMarkup + messageMarkup + pendingMarkup;
            bindRetryButtons();
            bindImageLightbox();

            const refreshedLoadOlder = document.getElementById('chatLoadOlder');
            if (refreshedLoadOlder) {
                refreshedLoadOlder.classList.toggle('hidden', !loadingOlder);
            }
            latestMessageId = mergedMessages.length ? mergedMessages[mergedMessages.length - 1].id || 0 : 0;
            updateSeenState(mergedMessages);
            scrollToBottom(Boolean(forceScroll));
        }

        function updateSeenState(messages) {
            if (!chatMessages) return;
            chatMessages.querySelectorAll('.chat-bubble .chat-meta-status[data-read-status="true"]').forEach((node) => {
                node.remove();
            });
            const ownMessages = messages.filter((message) => message.sender_id === currentUserId);
            if (!ownMessages.length) return;
            const lastOwn = ownMessages[ownMessages.length - 1];
            const bubble = chatMessages.querySelector(`[data-message-id="${lastOwn.id}"]`);
            const meta = bubble ? bubble.querySelector('.chat-meta') : null;
            if (!meta) return;
            const status = document.createElement('span');
            status.className = 'chat-meta-status';
            status.dataset.readStatus = 'true';
            status.textContent = lastOwn.is_read ? 'Seen' : 'Sent';
            meta.appendChild(status);
        }

        function setTypingIndicator(isTyping) {
            if (!typingIndicator) return;
            typingIndicator.classList.toggle('hidden', !isTyping);
        }

        async function sendTypingState(isTyping) {
            if (!typingUrl) return;
            try {
                await fetch(typingUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify({ typing: Boolean(isTyping) })
                });
            } catch (error) {
                console.error(error);
            }
        }

        function scheduleTypingHeartbeat() {
            if (!typingUrl) return;
            if (!typingSent) {
                typingSent = true;
                sendTypingState(true);
            }
            if (typingTimer) {
                window.clearTimeout(typingTimer);
            }
            typingTimer = window.setTimeout(() => {
                typingSent = false;
                sendTypingState(false);
            }, 2500);
        }

        function appendMessage(message) {
            if (!chatMessages || !message) return;

            const emptyState = document.getElementById('chatEmptyState');
            if (emptyState) {
                emptyState.remove();
            }

            const article = document.createElement('article');
            article.className = `chat-bubble ${message.sender_id === currentUserId ? 'is-own' : 'is-other'}`;
            article.dataset.messageId = message.id || '';

            const bubbleInner = document.createElement('div');
            bubbleInner.className = 'chat-bubble-inner';

            bubbleInner.innerHTML = buildAttachmentMarkup(message);

            if (message.content) {
                const p = document.createElement('p');
                p.className = 'chat-text';
                p.textContent = message.content;
                bubbleInner.appendChild(p);
            }

            const meta = document.createElement('div');
            meta.className = 'chat-meta';
            meta.innerHTML = `<span>${formatTime(message.created_at)}</span>`;

            article.appendChild(bubbleInner);
            article.appendChild(meta);
            chatMessages.appendChild(article);
            bindImageLightbox();
            latestMessageId = Math.max(latestMessageId, Number(message.id || 0));
            scrollToBottom(true);
        }

        function bindImageLightbox() {
            if (!chatMessages || !imageLightbox || !imageLightboxImg) return;
            chatMessages.querySelectorAll('.chat-image').forEach((img) => {
                img.onclick = function() {
                    imageLightboxImg.src = img.src;
                    imageLightbox.classList.remove('hidden');
                    document.body.style.overflow = 'hidden';
                };
            });
        }

        function findPendingNode(pendingId) {
            if (!chatMessages || !pendingId) return null;
            return chatMessages.querySelector(`[data-pending-id="${pendingId}"]`);
        }

        function addPendingMessage({ content, previewUrl, attachmentType, attachmentName, formData }) {
            const pendingMessage = {
                pending_id: createPendingId(),
                id: '',
                sender_id: currentUserId,
                content: content || '',
                image_path: previewUrl || '',
                attachment_name: attachmentName || '',
                message_type: attachmentType || 'text',
                created_at: new Date().toISOString(),
                meta_label: 'Sending...',
                failed: false,
                progress: 0,
                retryFormData: formData,
            };
            pendingMessages.push(pendingMessage);
            const emptyState = document.getElementById('chatEmptyState');
            if (emptyState) {
                emptyState.remove();
            }
            chatMessages.insertAdjacentHTML('beforeend', buildMessageMarkup(pendingMessage, 'is-pending'));
            bindRetryButtons();
            scrollToBottom(true);
            return pendingMessage.pending_id;
        }

        function resolvePendingMessage(pendingId, savedMessage) {
            pendingMessages = pendingMessages.filter((item) => item.pending_id !== pendingId);
            const pendingNode = findPendingNode(pendingId);
            if (pendingNode) {
                pendingNode.remove();
            }
            if (savedMessage) {
                appendMessage(savedMessage);
            }
        }

        function markPendingFailed(pendingId, errorMessage) {
            pendingMessages = pendingMessages.map((item) => (
                item.pending_id === pendingId
                    ? { ...item, failed: true, meta_label: errorMessage || 'Failed to send' }
                    : item
            ));
            const pending = pendingMessages.find((item) => item.pending_id === pendingId);
            const node = findPendingNode(pendingId);
            if (pending && node) {
                node.outerHTML = buildMessageMarkup(pending, 'is-failed');
                bindRetryButtons();
                scrollToBottom(true);
            }
        }

        function updatePendingProgress(pendingId, progressPercent) {
            pendingMessages = pendingMessages.map((item) => (
                item.pending_id === pendingId
                    ? {
                        ...item,
                        meta_label: progressPercent >= 100 ? 'Processing...' : `Uploading ${progressPercent}%`
                    }
                    : item
            ));
            const pending = pendingMessages.find((item) => item.pending_id === pendingId);
            const node = findPendingNode(pendingId);
            if (pending && node) {
                node.outerHTML = buildMessageMarkup(pending, pending.failed ? 'is-failed' : 'is-pending');
                bindRetryButtons();
            }
        }

        function sendMessageRequest(formData, pendingId) {
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', sendUrl, true);
                xhr.withCredentials = true;
                xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                xhr.setRequestHeader('Accept', 'application/json');

                xhr.upload.onprogress = function(event) {
                    if (!event.lengthComputable) return;
                    const percent = Math.max(1, Math.min(100, Math.round((event.loaded / event.total) * 100)));
                    updatePendingProgress(pendingId, percent);
                };

                xhr.onload = function() {
                    const payload = (() => {
                        try {
                            return JSON.parse(xhr.responseText || '{}');
                        } catch (_error) {
                            return {};
                        }
                    })();
                    if (xhr.status < 200 || xhr.status >= 300 || !payload.ok) {
                        reject(new Error(payload.error || 'Failed to send message'));
                        return;
                    }
                    resolvePendingMessage(pendingId, payload.message || null);
                    resolve(payload);
                };

                xhr.onerror = function() {
                    reject(new Error('Network error while sending message'));
                };

                xhr.send(formData);
            });
        }

        function bindRetryButtons() {
            if (!chatMessages) return;
            chatMessages.querySelectorAll('[data-chat-retry="true"]').forEach((button) => {
                button.onclick = async function() {
                    const bubble = button.closest('[data-pending-id]');
                    const pendingId = bubble ? bubble.dataset.pendingId : '';
                    const pending = pendingMessages.find((item) => item.pending_id === pendingId);
                    if (!pending || !pending.retryFormData || isSending) return;

                    try {
                        isSending = true;
                        pending.failed = false;
                        pending.meta_label = 'Sending...';
                        renderMessages(loadedMessages, true);
                        if (liveStatus) liveStatus.textContent = 'Sending...';
                        await sendMessageRequest(pending.retryFormData, pending.pending_id);
                        if (liveStatus) liveStatus.textContent = 'Live';
                    } catch (error) {
                        console.error(error);
                        markPendingFailed(pending.pending_id, error.message || 'Failed to send');
                        if (liveStatus) liveStatus.textContent = 'Send failed';
                    } finally {
                        isSending = false;
                    }
                };
            });
        }

        async function fetchMessages(silent) {
            if (activeConversationId !== conversationId) return;
            try {
                const response = await fetch(`${messagesUrl}?page=1`, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                if (!response.ok) throw new Error('Failed to fetch messages');

                const data = await response.json();
                const messages = data.messages || [];
                const newestId = messages.length ? messages[messages.length - 1].id : 0;
                const shouldRender = !silent || newestId !== latestMessageId;
                totalAvailablePages = Number(data.pages || totalAvailablePages || 1);
                mergeMessages(messages);

                if (shouldRender) {
                    if (data.unread_marker_id) {
                        unreadMarkerId = Number(data.unread_marker_id || unreadMarkerId);
                    } else if (messages.some((message) => message.is_read)) {
                        unreadMarkerId = 0;
                    }
                    renderMessages(loadedMessages, newestId !== latestMessageId);
                }
                setTypingIndicator(Boolean(data.other_user_typing));
                if (liveStatus) liveStatus.textContent = 'Live';
            } catch (error) {
                console.error(error);
                if (liveStatus) liveStatus.textContent = 'Reconnecting...';
            }
        }

        async function loadOlderMessages() {
            if (loadingOlder || highestLoadedPage >= totalAvailablePages) return;
            loadingOlder = true;
            renderMessages(loadedMessages, false);
            const previousHeight = chatMessages ? chatMessages.scrollHeight : 0;
            const nextPage = highestLoadedPage + 1;
            try {
                const response = await fetch(`${messagesUrl}?page=${nextPage}`, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                if (!response.ok) throw new Error('Failed to fetch older messages');
                const data = await response.json();
                mergeMessages(data.messages || []);
                highestLoadedPage = Number(data.page || nextPage);
                totalAvailablePages = Number(data.pages || totalAvailablePages || 1);
                renderMessages(loadedMessages, false);
                if (chatMessages) {
                    const newHeight = chatMessages.scrollHeight;
                    chatMessages.scrollTop = newHeight - previousHeight + chatMessages.scrollTop;
                }
            } catch (error) {
                console.error(error);
            } finally {
                loadingOlder = false;
                renderMessages(loadedMessages, false);
            }
        }

        function clearAttachmentPreview() {
            if (!attachmentPreview) return;
            attachmentPreview.innerHTML = '';
            attachmentPreview.classList.add('hidden');
        }

        function previewSelectedAttachment(file, kind) {
            if (!attachmentPreview) return;
            if (!file) {
                clearAttachmentPreview();
                return;
            }

            if (kind === 'image') {
                const reader = new FileReader();
                reader.onload = function(e) {
                    attachmentPreview.innerHTML = `<img src="${e.target.result}" alt="Preview"><span class="chat-preview-remove" id="removeChatAttachment">Remove</span>`;
                    attachmentPreview.classList.remove('hidden');
                    const removeBtn = document.getElementById('removeChatAttachment');
                    if (removeBtn) {
                        removeBtn.addEventListener('click', function() {
                            if (imageInput) imageInput.value = '';
                            clearAttachmentPreview();
                        });
                    }
                };
                reader.readAsDataURL(file);
                return;
            }

            attachmentPreview.innerHTML = `
                <div class="chat-preview-file">
                    <span class="chat-file-icon">FILE</span>
                    <span class="chat-preview-file-name">${escapeHtml(file.name || 'attachment')}</span>
                </div>
                <span class="chat-preview-remove" id="removeChatAttachment">Remove</span>
            `;
            attachmentPreview.classList.remove('hidden');
            const removeBtn = document.getElementById('removeChatAttachment');
            if (removeBtn) {
                removeBtn.addEventListener('click', function() {
                    if (fileInput) fileInput.value = '';
                    clearAttachmentPreview();
                });
            }
        }

        imageInput?.addEventListener('change', function() {
            const selectedFile = this.files && this.files[0] ? this.files[0] : null;
            if (selectedFile && fileInput) {
                fileInput.value = '';
            }
            previewSelectedAttachment(selectedFile, 'image');
        });

        fileInput?.addEventListener('change', function() {
            const selectedFile = this.files && this.files[0] ? this.files[0] : null;
            if (selectedFile && imageInput) {
                imageInput.value = '';
            }
            previewSelectedAttachment(selectedFile, 'file');
        });

        if (messageInput) {
            messageInput.addEventListener('input', function() {
                this.style.height = '46px';
                this.style.height = `${Math.min(this.scrollHeight, 140)}px`;
                if (this.value.trim()) {
                    scheduleTypingHeartbeat();
                } else if (typingSent) {
                    typingSent = false;
                    sendTypingState(false);
                }
            });
            messageInput.addEventListener('keydown', function(event) {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    if (!isSending) {
                        chatForm?.requestSubmit();
                    }
                }
            });
            window.setTimeout(() => {
                if (document.body.contains(messageInput)) {
                    messageInput.focus();
                }
            }, 20);
        }

        if (chatForm) {
            chatForm.setAttribute('data-turbo', 'false');
            chatForm.action = sendUrl;
            chatForm.noValidate = true;
        }

        chatForm?.addEventListener('submit', async function(event) {
            event.preventDefault();
            event.stopPropagation();
            if (isSending) return;
            const formData = new FormData(chatForm);
            const messageText = (formData.get('message') || '').toString().trim();
            const imageFile = formData.get('image');
            const attachmentFile = formData.get('attachment');
            const hasImage = Boolean(imageFile && imageFile.name);
            const hasAttachment = Boolean(attachmentFile && attachmentFile.name);

            if (!messageText && !hasImage && !hasAttachment) {
                return;
            }

            try {
                isSending = true;
                if (sendButton) {
                    sendButton.disabled = true;
                    sendButton.textContent = 'Sending...';
                }
                if (liveStatus) liveStatus.textContent = 'Sending...';
                const requestFormData = new FormData(chatForm);
                const previewUrl = hasImage
                    ? URL.createObjectURL(imageFile)
                    : (hasAttachment ? URL.createObjectURL(attachmentFile) : '');
                const pendingId = addPendingMessage({
                    content: messageText,
                    previewUrl,
                    attachmentType: hasImage ? 'image' : (hasAttachment ? 'file' : 'text'),
                    attachmentName: hasAttachment ? attachmentFile.name : '',
                    formData: requestFormData
                });

                chatForm.reset();
                if (messageInput) {
                    messageInput.style.height = '46px';
                    messageInput.focus();
                }
                clearAttachmentPreview();
                if (typingSent) {
                    typingSent = false;
                    sendTypingState(false);
                }
                await sendMessageRequest(formData, pendingId);
                if (liveStatus) liveStatus.textContent = 'Live';
            } catch (error) {
                console.error(error);
                const failedPending = pendingMessages[pendingMessages.length - 1];
                if (failedPending && !failedPending.failed) {
                    markPendingFailed(failedPending.pending_id, error.message || 'Failed to send');
                }
                if (liveStatus) liveStatus.textContent = 'Send failed';
            } finally {
                isSending = false;
                if (sendButton) {
                    sendButton.disabled = false;
                    sendButton.textContent = 'Send';
                }
            }
        });
        fetchMessages(false);
        chatMessages?.addEventListener('scroll', function() {
            if (chatMessages.scrollTop < 80) {
                loadOlderMessages();
            }
        });
        imageLightboxClose?.addEventListener('click', function() {
            imageLightbox.classList.add('hidden');
            imageLightboxImg.src = '';
            document.body.style.overflow = '';
        });
        imageLightbox?.addEventListener('click', function(event) {
            if (event.target === imageLightbox) {
                imageLightbox.classList.add('hidden');
                imageLightboxImg.src = '';
                document.body.style.overflow = '';
            }
        });
        chatPollTimer = window.setInterval(function() {
            fetchMessages(true);
        }, 2500);

        window.addEventListener('beforeunload', function() {
            if (typingSent) {
                sendTypingState(false);
            }
        }, { once: true });
    }

    document.addEventListener('DOMContentLoaded', initChatPage);
    document.addEventListener('turbo:load', initChatPage);
    document.addEventListener('turbo:before-cache', function() {
        const chatPage = document.querySelector('.chat-page');
        if (chatPage) {
            delete chatPage.dataset.chatInitialized;
        }
        clearChatPolling();
    });
    if (document.readyState !== 'loading') {
        initChatPage();
    }
})();
