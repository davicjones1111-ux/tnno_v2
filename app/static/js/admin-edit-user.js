(function() {
    'use strict';

    function initAdminEditUser() {
        const sellerToggle = document.getElementById('seller');
        const commissionGroup = document.getElementById('commission-group');
        if (!sellerToggle || !commissionGroup || sellerToggle.dataset.bound === '1') return;

        sellerToggle.dataset.bound = '1';

        const syncVisibility = () => {
            commissionGroup.style.display = sellerToggle.checked ? 'block' : 'none';
        };

        sellerToggle.addEventListener('change', syncVisibility);
        syncVisibility();
    }

    document.addEventListener('turbo:load', initAdminEditUser);
    document.addEventListener('turbo:render', initAdminEditUser);
    document.addEventListener('DOMContentLoaded', initAdminEditUser);
})();
