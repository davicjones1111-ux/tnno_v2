(function() {
    'use strict';

    function initSignupForm() {
        const form = document.getElementById('signup-form');
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        const usernameInput = document.getElementById('username');
        const passwordInput = document.getElementById('password');
        const confirmInput = document.getElementById('confirm_password');
        const messageEl = document.getElementById('username-message');
        const submitBtn = document.getElementById('signup-submit');
        const checkUrl = form.dataset.checkUsernameUrl;
        const minPasswordLength = parseInt(form.dataset.minPasswordLength || '8', 10);

        function setSubmitEnabled(enabled) {
            submitBtn.disabled = !enabled;
        }

        function getResponseData(data) {
            return data && typeof data === 'object' ? (data.data || data) : {};
        }

        function setMessage(text, isAvailable) {
            messageEl.textContent = text || '';
            messageEl.style.color = isAvailable ? '#1f7a3a' : '#b42318';
        }

        usernameInput.addEventListener('input', function() {
            const username = this.value.trim();
            if (username.length < 3) {
                messageEl.textContent = '';
                messageEl.style.color = '';
                setSubmitEnabled(true);
                return;
            }

            setSubmitEnabled(true);
            csrfFetch(checkUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `username=${encodeURIComponent(username)}`
            })
                .then((response) => {
                    if (!response.ok) throw new Error('bad response');
                    return response.json();
                })
                .then((data) => {
                    const payload = getResponseData(data);
                    const available = Boolean(payload.available);
                    setMessage(payload.message || '', available);
                    setSubmitEnabled(true);
                })
                .catch(() => {
                    setMessage('Availability check unavailable. You can still submit the form.', false);
                    setSubmitEnabled(true);
                });
        });

        form.addEventListener('submit', (event) => {
            const password = passwordInput.value;
            const confirmPassword = confirmInput.value;

            if (password.length < minPasswordLength) {
                event.preventDefault();
                alert(`Password must be at least ${minPasswordLength} characters.`);
                return;
            }

            if (password !== confirmPassword) {
                event.preventDefault();
                alert('Passwords do not match.');
                return;
            }
        });

        setSubmitEnabled(true);
    }

    document.addEventListener('DOMContentLoaded', initSignupForm);
    document.addEventListener('turbo:load', initSignupForm);
})();
