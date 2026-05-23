(function() {
    const storePage = document.querySelector('.store-page');
    if (!storePage) return;

    let currentPage = Number(storePage.dataset.initialPage || 1);
    let isLoading = false;
    let hasMore = (storePage.dataset.initialHasNext || 'false') === 'true';
    let searchQuery = storePage.dataset.initialSearch || '';
    let sellerQuery = storePage.dataset.initialSeller || '';
    let typeFilter = storePage.dataset.initialType || '';
    let sortBy = storePage.dataset.initialSort || 'latest';
    const limit = 12;
    const apiBaseUrl = storePage.dataset.apiUrl;
    const pageBaseUrl = storePage.dataset.pageUrl;

    const feed = document.getElementById('productsFeed');
    const loadingIndicator = document.getElementById('loadingIndicator');
    const endMessage = document.getElementById('endMessage');
    const emptyState = document.getElementById('emptyState');
    const loadMoreContainer = document.getElementById('loadMoreContainer');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const productSearch = document.getElementById('productSearch');
    const sellerSearch = document.getElementById('sellerSearch');
    const sortSelect = document.getElementById('sortSelect');
    const clearSearchBtn = document.getElementById('clearSearchBtn');

    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    function buildApiUrl(page, overrideParams = {}) {
        const params = new URLSearchParams({
            page,
            limit,
            sort: sortBy
        });

        if (typeFilter) params.set('type', typeFilter);
        if (searchQuery) params.set('search', searchQuery);
        if (sellerQuery) params.set('seller', sellerQuery);

        Object.entries(overrideParams).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') {
                params.delete(key);
            } else {
                params.set(key, value);
            }
        });

        return `${apiBaseUrl}?${params.toString()}`;
    }

    function buildPageUrl(overrideParams = {}) {
        const params = new URLSearchParams();
        if (searchQuery) params.set('search', searchQuery);
        if (sellerQuery) params.set('seller', sellerQuery);
        if (typeFilter) params.set('type', typeFilter);
        if (sortBy && sortBy !== 'latest') params.set('sort', sortBy);

        Object.entries(overrideParams).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') {
                params.delete(key);
            } else {
                params.set(key, value);
            }
        });

        const qs = params.toString();
        return qs ? `${pageBaseUrl}?${qs}` : pageBaseUrl;
    }

    async function loadProducts() {
        if (isLoading || !hasMore) return;

        isLoading = true;
        if (loadingIndicator) loadingIndicator.style.display = 'flex';

        try {
            const response = await fetch(buildApiUrl(currentPage + 1), {
                credentials: 'same-origin'
            });
            if (!response.ok) throw new Error('Failed to load products');

            const data = await response.json();
            if (data.products && data.products.length > 0) {
                renderProducts(data.products);
                currentPage = data.page;
                hasMore = data.has_next;
                if (!hasMore && endMessage) {
                    endMessage.style.display = 'block';
                }
            } else {
                hasMore = false;
                if (endMessage) endMessage.style.display = 'block';
            }
        } catch (error) {
            console.error('Error loading products:', error);
            if (loadMoreContainer) loadMoreContainer.style.display = 'block';
        } finally {
            isLoading = false;
            if (loadingIndicator) loadingIndicator.style.display = 'none';
        }
    }

    function renderProducts(products) {
        if (!feed) return;

        products.forEach((product) => {
            const card = document.createElement('div');
            card.className = 'product-card';
            card.dataset.productId = product.id;

            const productType = product.product_type || 'digital';
            const typeBadge = `<div class="product-type-badge ${productType}">${productType}</div>`;
            const stockBadge = product.quantity === 0
                ? '<div class="sold-out-badge">Sold Out</div>'
                : product.quantity <= 3
                    ? `<div class="low-stock-badge">Only ${product.quantity} left!</div>`
                    : '';

            const sellerHtml = product.seller_username
                ? `<a href="/store/seller/${product.seller_id}" class="seller-link">
                    <span class="seller-avatar">${product.seller_username[0].toUpperCase()}</span>
                    ${product.seller_username}
                </a>`
                : '<span class="seller-link">Sold by: Admin</span>';

            const feedback = product.feedback || {};
            const roundedStars = Math.round(Number(feedback.avg_rating || 0));
            let starsHtml = '';
            for (let i = 1; i <= 5; i += 1) {
                starsHtml += i <= roundedStars ? '★' : '☆';
            }

            const actionBtn = product.quantity > 0
                ? `<a href="/store/product/${product.id}" class="px-btn px-btn-primary px-btn-sm">Buy Now</a>`
                : '<button class="px-btn px-btn-secondary px-btn-sm" disabled>Sold Out</button>';

            const description = product.description ? product.description.substring(0, 80) : '';
            const descSuffix = product.description && product.description.length > 80 ? '...' : '';

            card.innerHTML = `
                <div class="product-image-container">
                    ${product.image_filename
                        ? `<img src="/static/uploads/merch/${product.image_filename}" alt="${product.name}" class="product-image" loading="lazy">`
                        : '<div class="product-image-placeholder"><span>🎁</span></div>'}
                    ${typeBadge}
                    ${stockBadge}
                </div>
                <div class="product-content">
                    <h3 class="product-title">${product.name}</h3>
                    <p class="product-description">${description}${descSuffix}</p>
                    <div class="product-seller">${sellerHtml}</div>
                    <div class="product-feedback-meta">
                        <span class="feedback-chip">${starsHtml} ${Number(feedback.rating_count || 0)}</span>
                        <span class="feedback-chip">Review ${Number(feedback.review_count || 0)}</span>
                        <span class="feedback-chip">Like ${Number(feedback.like_count || 0)}</span>
                        <span class="feedback-chip">Dislike ${Number(feedback.dislike_count || 0)}</span>
                    </div>
                    <div class="product-footer">
                        <div class="product-price">
                            <span class="px-coin"></span>
                            <span class="price-value">${product.price}</span>
                        </div>
                        ${actionBtn}
                    </div>
                </div>
            `;

            feed.appendChild(card);
        });
    }

    async function reloadProducts() {
        currentPage = 1;
        hasMore = true;

        feed?.querySelectorAll('.product-card').forEach((card) => card.remove());
        emptyState?.remove();

        if (endMessage) endMessage.style.display = 'none';
        if (loadMoreContainer) loadMoreContainer.style.display = 'none';
        if (loadingIndicator) loadingIndicator.style.display = 'flex';

        try {
            const response = await fetch(buildApiUrl(1), {
                credentials: 'same-origin'
            });
            if (!response.ok) throw new Error('Failed to load products');

            const data = await response.json();
            if (data.products && data.products.length > 0) {
                renderProducts(data.products);
                currentPage = data.page;
                hasMore = data.has_next;
                if (!hasMore && endMessage) {
                    endMessage.style.display = 'block';
                }
            } else if (feed) {
                const empty = document.createElement('div');
                empty.className = 'empty-state';
                empty.innerHTML = `
                    <div class="empty-icon">🔍</div>
                    <h3>No Results</h3>
                    <p class="px-text-muted">No products match your search.</p>
                `;
                feed.appendChild(empty);
            }
        } catch (error) {
            console.error('Error:', error);
        } finally {
            if (loadingIndicator) loadingIndicator.style.display = 'none';
        }
    }

    const handleProductSearch = debounce(function(query) {
        searchQuery = query;
        window.history.pushState({}, '', buildPageUrl({ search: query || undefined, page: undefined }));
        reloadProducts();
    }, 400);

    const handleSellerSearch = debounce(function(query) {
        sellerQuery = query;
        window.history.pushState({}, '', buildPageUrl({ seller: query || undefined, page: undefined }));
        reloadProducts();
    }, 400);

    productSearch?.addEventListener('input', function() {
        handleProductSearch(this.value.trim());
    });

    sellerSearch?.addEventListener('input', function() {
        handleSellerSearch(this.value.trim());
    });

    function updateSort() {
        if (!sortSelect) return;
        sortBy = sortSelect.value;
        window.history.pushState({}, '', buildPageUrl({ page: undefined }));
        reloadProducts();
    }

    function clearSearch() {
        if (productSearch) {
            productSearch.value = '';
        }
        searchQuery = '';
        window.history.pushState({}, '', buildPageUrl({ search: undefined, page: undefined }));
        reloadProducts();
    }

    sortSelect?.addEventListener('change', updateSort);
    clearSearchBtn?.addEventListener('click', clearSearch);

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && hasMore && !isLoading && !searchQuery && !sellerQuery) {
            loadProducts();
        }
    }, { rootMargin: '150px' });

    if (endMessage) {
        observer.observe(endMessage);
    }

    loadMoreBtn?.addEventListener('click', function() {
        this.disabled = true;
        this.textContent = 'Loading...';
        loadProducts().finally(() => {
            this.disabled = false;
            this.textContent = 'Load More Products';
        });
    });

    if (!hasMore && endMessage) {
        endMessage.style.display = 'block';
    }
})();
