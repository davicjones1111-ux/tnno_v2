(function(global) {
    'use strict';

    const app = global.TNNOApp || {};
    let chatPollTimer = null;
    let activeConversationId = 0;

    function clearChatPolling() {
        if (chatPollTimer) {
            global.clearInterval(chatPollTimer);
            chatPollTimer = null;
        }
        activeConversationId = 0;
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

    function normalizeAttachmentUrl(value) {
        const raw = app.toText(value).trim();
        if (!raw) return '';
        if (raw.startsWith('blob:')) return raw;
        return app.mediaUrl(raw);
    }

    function getAttachmentName(message) {
        if (message.attachment_name) return app.toText(message.attachment_name);
        const imagePath = message.image_path || '';
        if (!imagePath) return 'attachment';
        const cleanPath = app.toText(imagePath).split('?')[0].replace(/\/+$/, '');
        const parts = cleanPath.split('/');
        return parts[parts.length - 1] || 'attachment';
    }

    function createLoadOlderIndicator(isVisible) {
        const indicator = app.createElement('div', {
            className: `chat-load-older${isVisible ? '' : ' hidden'}`,
            attrs: { id: 'chatLoadOlder' },
            text: 'Loading older messages...'
        });
        return indicator;
    }

    function createDateSeparator(text) {
        return app.createElement('div', {
            className: 'chat-date-separator',
            text
        });
    }

    function createUnreadSeparator() {
        return app.createElement('div', {
            className: 'chat-unread-separator',
            dataset: { unreadSeparator: 'true' },
            text: 'New messages'
        });
    }

    function createEmptyState() {
        const empty = app.createElement('div', {
            className: 'chat-empty',
            attrs: { id: 'chatEmptyState' }
        });
        empty.appendChild(app.createElement('div', { className: 'chat-empty-icon', text: '✉️' }));
        empty.appendChild(app.createElement('p', { text: 'No messages yet. Start the chat.' }));
        return empty;
    }

    function buildAttachmentNode(message) {
        if (!message.image_path) return null;
        const attachmentUrl = normalizeAttachmentUrl(message.image_path);
        if (!attachmentUrl) return null;
        if (message.message_type === 'image') {
            return app.createElement('img', {
                className: 'chat-image',
                attrs: {
                    src: attachmentUrl,
                    alt: 'Chat image'
                }
            });
        }
        if (message.message_type === 'file') {
            const link = app.createElement('a', {
                className: 'chat-file-chip',
                attrs: {
                    href: attachmentUrl,
                    target: '_blank',
                    rel: 'noopener',
                    download: ''
                }
            });
            link.appendChild(app.createElement('span', {
                className: 'chat-file-icon',
                text: 'FILE'
            }));
            const meta = app.createElement('span', { className: 'chat-file-meta' });
            meta.appendChild(app.createElement('strong', { text: getAttachmentName(message) }));
            meta.appendChild(app.createElement('small', { text: 'Open attachment' }));
            link.appendChild(meta);
            return link;
        }
        return null;
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

        function buildMessageElement(message, extraClass) {
            const ownClass = message.sender_id === currentUserId ? 'is-own' : 'is-other';
            const article = app.createElement('article', {
                className: `chat-bubble ${ownClass}${extraClass ? ` ${extraClass}` : ''}`,
                dataset: {
                    messageId: message.id || '',
                    pendingId: message.pending_id || ''
                }
            });

            const bubbleInner = app.createElement('div', { className: 'chat-bubble-inner' });
            const attachmentNode = buildAttachmentNode(message);
            if (attachmentNode) {
                bubbleInner.appendChild(attachmentNode);
            }
            if (message.content) {
                bubbleInner.appendChild(app.createElement('p', {
                    className: 'chat-text',
                    text: message.content
                }));
            }

            const meta = app.createElement('div', { className: 'chat-meta' });
            meta.appendChild(app.createElement('span', {
                text: formatTime(message.created_at)
            }));
            if (message.meta_label) {
                meta.appendChild(app.createElement('span', {
                    className: 'chat-meta-status',
                    text: message.meta_label
                }));
            }
            if (extraClass === 'is-failed') {
                meta.appendChild(app.createElement('button', {
                    className: 'chat-retry-btn',
                    text: 'Retry',
                    attrs: {
                        type: 'button',
                        'data-chat-retry': 'true'
                    }
                }));
            }

            article.appendChild(bubbleInner);
            article.appendChild(meta);
            return article;
        }

        function sortMessages(messages) {
            return [...messages].sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
        }

        function mergeMessages(messages) {
            const byId = new Map(loadedMessages.map((message) => [Number(message.id), message]));
            messages.forEach((message) => {
                byId.set(Number(message.id), message);
            });
            loadedMessages = sortMessages(Array.from(byId.values()));
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
            meta.appendChild(app.createElement('span', {
                className: 'chat-meta-status',
                dataset: { readStatus: 'true' },
                text: lastOwn.is_read ? 'Seen' : 'Sent'
            }));
        }

        function renderMessages(messages, forceScroll) {
            if (!chatMessages) return;
            const mergedMessages = sortMessages(messages);
            app.clearChildren(chatMessages);

            if (!mergedMessages.length && !pendingMessages.length) {
                chatMessages.appendChild(createEmptyState());
                latestMessageId = 0;
                return;
            }

            chatMessages.appendChild(createLoadOlderIndicator(loadingOlder));
            let unreadInserted = false;
            let lastDateLabel = '';

            mergedMessages.forEach((message) => {
                const currentDateLabel = formatDateLabel(message.created_at);
                if (currentDateLabel && currentDateLabel !== lastDateLabel) {
                    chatMessages.appendChild(createDateSeparator(currentDateLabel));
                    lastDateLabel = currentDateLabel;
                }
                if (!unreadInserted && unreadMarkerId && Number(message.id) === Number(unreadMarkerId)) {
                    chatMessages.appendChild(createUnreadSeparator());
                    unreadInserted = true;
                }
                chatMessages.appendChild(buildMessageElement(message, ''));
            });

            pendingMessages.forEach((message) => {
                chatMessages.appendChild(buildMessageElement(message, message.failed ? 'is-failed' : 'is-pending'));
            });

            latestMessageId = mergedMessages.length ? mergedMessages[mergedMessages.length - 1].id || 0 : 0;
            updateSeenState(mergedMessages);
            scrollToBottom(Boolean(forceScroll));
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
                global.clearTimeout(typingTimer);
            }
            typingTimer = global.setTimeout(() => {
                typingSent = false;
                sendTypingState(false);
            }, 2500);
        }

        function addPendingMessage(payload) {
            const pendingMessage = {
                pending_id: createPendingId(),
                id: '',
                sender_id: currentUserId,
                content: payload.content || '',
                image_path: payload.previewUrl || '',
                attachment_name: payload.attachmentName || '',
                message_type: payload.attachmentType || 'text',
                created_at: new Date().toISOString(),
                meta_label: 'Sending...',
                failed: false,
                progress: 0,
                retryFormData: payload.formData,
                object_url: payload.previewUrl || ''
            };
            pendingMessages.push(pendingMessage);
            renderMessages(loadedMessages, true);
            return pendingMessage.pending_id;
        }

        function revokePreviewUrl(item) {
            if (item && item.object_url && item.object_url.startsWith('blob:')) {
                try {
                    global.URL.revokeObjectURL(item.object_url);
                } catch (_error) {
                    // Ignore revoke failures for already-released URLs.
                }
            }
        }

        function resolvePendingMessage(pendingId, savedMessage) {
            const resolved = pendingMessages.find((item) => item.pending_id === pendingId);
            revokePreviewUrl(resolved);
            pendingMessages = pendingMessages.filter((item) => item.pending_id !== pendingId);
            if (savedMessage) {
                mergeMessages([savedMessage]);
            }
            renderMessages(loadedMessages, true);
        }

        function markPendingFailed(pendingId, errorMessage) {
            pendingMessages = pendingMessages.map((item) => (
                item.pending_id === pendingId
                    ? { ...item, failed: true, meta_label: errorMessage || 'Failed to send' }
                    : item
            ));
            renderMessages(loadedMessages, true);
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
            renderMessages(loadedMessages, false);
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

        async function retryPendingMessage(pendingId) {
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
            app.clearChildren(attachmentPreview);
            attachmentPreview.classList.add('hidden');
        }

        function buildPreviewRemoveButton(onClick) {
            const removeBtn = app.createElement('button', {
                className: 'chat-preview-remove',
                text: 'Remove',
                attrs: { type: 'button' }
            });
            removeBtn.addEventListener('click', onClick);
            return removeBtn;
        }

        function previewSelectedAttachment(file, kind) {
            if (!attachmentPreview) return;
            if (!file) {
                clearAttachmentPreview();
                return;
            }

            app.clearChildren(attachmentPreview);
            attachmentPreview.classList.remove('hidden');

            if (kind === 'image') {
                const reader = new FileReader();
                reader.onload = function(event) {
                    app.clearChildren(attachmentPreview);
                    attachmentPreview.appendChild(app.createElement('img', {
                        attrs: {
                            src: app.toText(event.target && event.target.result),
                            alt: 'Preview'
                        }
                    }));
                    attachmentPreview.appendChild(buildPreviewRemoveButton(() => {
                        if (imageInput) imageInput.value = '';
                        clearAttachmentPreview();
                    }));
                };
                reader.readAsDataURL(file);
                return;
            }

            const filePreview = app.createElement('div', { className: 'chat-preview-file' });
            filePreview.appendChild(app.createElement('span', {
                className: 'chat-file-icon',
                text: 'FILE'
            }));
            filePreview.appendChild(app.createElement('span', {
                className: 'chat-preview-file-name',
                text: file.name || 'attachment'
            }));
            attachmentPreview.appendChild(filePreview);
            attachmentPreview.appendChild(buildPreviewRemoveButton(() => {
                if (fileInput) fileInput.value = '';
                clearAttachmentPreview();
            }));
        }

        imageInput?.addEventListener('change', function() {
            const selectedFile = this.files && this.files[0] ? this.files[0] : null;
            if (selectedFile && fileInput) fileInput.value = '';
            previewSelectedAttachment(selectedFile, 'image');
        });

        fileInput?.addEventListener('change', function() {
            const selectedFile = this.files && this.files[0] ? this.files[0] : null;
            if (selectedFile && imageInput) imageInput.value = '';
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
            global.setTimeout(() => {
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

        chatMessages?.addEventListener('click', (event) => {
            const retryBtn = event.target.closest('[data-chat-retry="true"]');
            if (retryBtn) {
                const bubble = retryBtn.closest('[data-pending-id]');
                const pendingId = bubble ? bubble.dataset.pendingId : '';
                retryPendingMessage(pendingId);
                return;
            }

            const image = event.target.closest('.chat-image');
            if (image && imageLightbox && imageLightboxImg) {
                imageLightboxImg.src = image.getAttribute('src') || '';
                imageLightbox.classList.remove('hidden');
                document.body.style.overflow = 'hidden';
            }
        });

        chatForm?.addEventListener('submit', async function(event) {
            event.preventDefault();
            event.stopPropagation();
            if (isSending) return;

            const formData = new FormData(chatForm);
            const messageText = app.toText(formData.get('message')).trim();
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
                    ? global.URL.createObjectURL(imageFile)
                    : (hasAttachment ? global.URL.createObjectURL(attachmentFile) : '');
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
        chatMessages?.addEventListener('scroll', () => {
            if (chatMessages.scrollTop < 80) {
                loadOlderMessages();
            }
        });
        imageLightboxClose?.addEventListener('click', () => {
            imageLightbox.classList.add('hidden');
            imageLightboxImg.src = '';
            document.body.style.overflow = '';
        });
        imageLightbox?.addEventListener('click', (event) => {
            if (event.target === imageLightbox) {
                imageLightbox.classList.add('hidden');
                imageLightboxImg.src = '';
                document.body.style.overflow = '';
            }
        });
        chatPollTimer = global.setInterval(() => {
            fetchMessages(true);
        }, 2500);

        global.addEventListener('beforeunload', () => {
            if (typingSent) {
                sendTypingState(false);
            }
        }, { once: true });
    }

    document.addEventListener('DOMContentLoaded', initChatPage);
    document.addEventListener('turbo:load', initChatPage);
    document.addEventListener('turbo:before-cache', () => {
        const chatPage = document.querySelector('.chat-page');
        if (chatPage) {
            delete chatPage.dataset.chatInitialized;
        }
        clearChatPolling();
    });
    if (document.readyState !== 'loading') {
        initChatPage();
    }
})(window);
