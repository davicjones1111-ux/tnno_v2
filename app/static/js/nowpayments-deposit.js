(function() {
    'use strict';

    function initNowPaymentsDepositForms() {
        document.querySelectorAll('[data-nowpayments-deposit-form]').forEach((form) => {
            if (form.dataset.nowpaymentsBound === '1') return;
            form.dataset.nowpaymentsBound = '1';
            form.setAttribute('data-turbo', 'false');
            form.setAttribute('target', '_top');

            const submitButton = form.querySelector('button[type="submit"]');
            const defaultText = submitButton ? submitButton.textContent : '';

            form.addEventListener('submit', () => {
                if (!submitButton) return;
                submitButton.disabled = true;
                submitButton.textContent = form.dataset.submittingText || 'Redirecting...';

                window.setTimeout(() => {
                    submitButton.disabled = false;
                    submitButton.textContent = defaultText;
                }, 15000);
            });
        });
    }

    document.addEventListener('turbo:load', initNowPaymentsDepositForms);
    document.addEventListener('turbo:render', initNowPaymentsDepositForms);
    document.addEventListener('DOMContentLoaded', initNowPaymentsDepositForms);
})();
