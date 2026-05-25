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

        usernameInput.addEventListener('input', function() {
            const username = this.value.trim();
            if (username.length < 3) {
                messageEl.textContent = '';
                setSubmitEnabled(false);
                return;
            }

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
                    messageEl.textContent = data.message || '';
                    if (data.available) {
                        messageEl.style.color = 'green';
                        setSubmitEnabled(true);
                    } else {
                        messageEl.style.color = 'red';
                        setSubmitEnabled(false);
                    }
                })
                .catch(() => {
                    messageEl.textContent = 'Error checking availability';
                    messageEl.style.color = 'red';
                    setSubmitEnabled(false);
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

            submitBtn.disabled = true;
            submitBtn.textContent = 'CREATING ACCOUNT...';
        });

        setSubmitEnabled(false);
    }

    document.addEventListener('DOMContentLoaded', initSignupForm);
    document.addEventListener('turbo:load', initSignupForm);
})();
