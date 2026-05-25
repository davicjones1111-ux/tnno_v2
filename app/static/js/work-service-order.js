(function(global) {
    'use strict';

    function initServiceOrderForm() {
        const form = document.getElementById('service-order-form');
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        const serviceCatalog = (global.TNNOApp && global.TNNOApp.parseJsonDataset(form, 'serviceCatalog', {})) || {};
        const premiumServices = new Set((global.TNNOApp && global.TNNOApp.parseJsonDataset(form, 'premiumServices', [])) || []);
        const premiumPrice = parseInt(form.dataset.premiumPrice || '0', 10);
        const engagementPrice = parseInt(form.dataset.engagementPrice || '0', 10);
        const minQty = parseInt(form.dataset.minQty || '1', 10);
        const maxQty = parseInt(form.dataset.maxQty || '1', 10);

        const categoryInput = document.getElementById('category');
        const serviceInput = document.getElementById('service');
        const quantityInput = document.getElementById('quantity');
        const chargeInput = document.getElementById('charge');

        function getUnitPrice(serviceName) {
            return premiumServices.has(serviceName) ? premiumPrice : engagementPrice;
        }

        function clampQuantity() {
            let qty = parseInt(quantityInput.value || minQty, 10);
            if (Number.isNaN(qty)) qty = minQty;
            if (qty < minQty) qty = minQty;
            if (qty > maxQty) qty = maxQty;
            quantityInput.value = qty;
            return qty;
        }

        function refillServices() {
            const selectedCategory = categoryInput.value;
            const services = serviceCatalog[selectedCategory] || [];
            const previous = serviceInput.value;

            serviceInput.replaceChildren();
            services.forEach((name) => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                serviceInput.appendChild(opt);
            });

            if (services.includes(previous)) {
                serviceInput.value = previous;
            }
        }

        function updateCharge() {
            const qty = clampQuantity();
            const selectedService = serviceInput.value;
            const unitPrice = getUnitPrice(selectedService);
            chargeInput.value = qty * unitPrice;
        }

        categoryInput.addEventListener('change', () => {
            refillServices();
            updateCharge();
        });
        serviceInput.addEventListener('change', updateCharge);
        quantityInput.addEventListener('input', updateCharge);

        refillServices();
        updateCharge();
    }

    document.addEventListener('DOMContentLoaded', initServiceOrderForm);
    document.addEventListener('turbo:load', initServiceOrderForm);
})(window);
