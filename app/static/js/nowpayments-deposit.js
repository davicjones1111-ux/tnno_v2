(function() {
    'use strict';

    function ensureFeedbackBox(form) {
        let feedback = form.querySelector('[data-nowpayments-feedback]');
        if (feedback) return feedback;

        feedback = document.createElement('div');
        feedback.setAttribute('data-nowpayments-feedback', '1');
        feedback.style.marginTop = '12px';
        form.appendChild(feedback);
        return feedback;
    }

    function showFeedback(form, message, category, paymentUrl) {
        const feedback = ensureFeedbackBox(form);
        feedback.innerHTML = '';

        const alertBox = document.createElement('div');
        alertBox.className = `px-alert px-alert-${category || 'info'}`;
        alertBox.setAttribute('role', 'alert');

        const messageText = document.createElement('span');
        messageText.textContent = message;
        alertBox.appendChild(messageText);

        if (paymentUrl) {
            const spacer = document.createElement('span');
            spacer.textContent = ' ';
            alertBox.appendChild(spacer);

            const link = document.createElement('a');
            link.href = paymentUrl;
            link.textContent = 'Open payment page';
            link.setAttribute('data-turbo', 'false');
            link.setAttribute('target', '_top');
            link.setAttribute('rel', 'noopener noreferrer');
            link.style.fontWeight = '700';
            link.style.marginLeft = '8px';
            alertBox.appendChild(link);
        }

        feedback.appendChild(alertBox);
    }

    function clearFeedback(form) {
        const feedback = ensureFeedbackBox(form);
        feedback.innerHTML = '';
    }

    function initNowPaymentsDepositForms() {
        document.querySelectorAll('[data-nowpayments-deposit-form]').forEach((form) => {
            if (form.dataset.nowpaymentsBound === '1') return;
            form.dataset.nowpaymentsBound = '1';
            form.setAttribute('data-turbo', 'false');
            form.setAttribute('target', '_top');

            const submitButton = form.querySelector('button[type="submit"]');
            const defaultText = submitButton ? submitButton.textContent : '';

            form.addEventListener('submit', async (event) => {
                if (typeof window.csrfFetch !== 'function') return;
                event.preventDefault();
                clearFeedback(form);

                if (form.dataset.submitting === '1') {
                    return;
                }
                form.dataset.submitting = '1';

                if (!submitButton) {
                    delete form.dataset.submitting;
                    form.submit();
                    return;
                }
                submitButton.disabled = true;
                submitButton.textContent = form.dataset.submittingText || 'Redirecting...';

                try {
                    const response = await window.csrfFetch(form.action, {
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        body: new FormData(form),
                    });
                    const payload = await response.json().catch(() => ({}));

                    if (!response.ok) {
                        throw new Error(payload.error || payload.message || 'Unable to open NowPayments right now.');
                    }

                    if (!payload.payment_url) {
                        if (response.redirected && response.url) {
                            window.top.location.assign(response.url);
                            return;
                        }
                        throw new Error('Payment provider did not return a checkout URL.');
                    }

                    showFeedback(
                        form,
                        'Opening NowPayments...',
                        'info',
                        payload.payment_url,
                    );
                    window.top.location.assign(payload.payment_url);
                    return;
                } catch (error) {
                    const message = error && error.message
                        ? error.message
                        : 'Unable to open NowPayments right now.';
                    showFeedback(form, message, 'error');
                } finally {
                    delete form.dataset.submitting;
                    submitButton.disabled = false;
                    submitButton.textContent = defaultText;
                }
            });
        });
    }

    document.addEventListener('turbo:load', initNowPaymentsDepositForms);
    document.addEventListener('turbo:render', initNowPaymentsDepositForms);
    document.addEventListener('DOMContentLoaded', initNowPaymentsDepositForms);
})();
