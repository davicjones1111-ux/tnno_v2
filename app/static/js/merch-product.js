(function() {
    const productPage = document.querySelector('.product-page');
    if (!productPage) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    const quantityInput = document.getElementById('quantity');
    const qtyDisplay = document.getElementById('qty-display');
    const totalDisplay = document.getElementById('total-display');
    const price = Number(productPage.dataset.productPrice || 0);
    const maxQuantity = Number(productPage.dataset.productQuantity || 0);
    const shareButton = document.getElementById('share-location-btn');
    const locationStatus = document.getElementById('location-status');
    const latInput = document.getElementById('shipping-lat');
    const lngInput = document.getElementById('shipping-lng');
    const locationTextInput = document.getElementById('shipping-location-text');
    const galleryButtons = document.querySelectorAll('.merch-gallery-thumb');
    const mainProductImage = document.getElementById('product-main-image');
    const ratingButtons = document.querySelectorAll('.product-star-button');
    const reactionButtons = document.querySelectorAll('[data-reaction-action]');
    const avgNode = document.querySelector('[data-feedback-rating-average]');
    const countNode = document.querySelector('[data-feedback-rating-count]');
    const likeNode = document.querySelector('[data-feedback-like]');
    const dislikeNode = document.querySelector('[data-feedback-dislike]');
    const reviewNode = document.querySelector('[data-feedback-review]');

    function setLocationReady(ready, message) {
        if (!locationStatus) return;
        locationStatus.textContent = message || (ready ? 'Location shared' : 'Location not shared');
        locationStatus.style.color = ready ? 'var(--px-green)' : 'var(--px-text-muted)';
    }

    function updateTotals() {
        if (!quantityInput) return;
        let qty = parseInt(quantityInput.value, 10) || 0;
        if (qty < 1) qty = 1;
        if (maxQuantity && qty > maxQuantity) qty = maxQuantity;
        quantityInput.value = qty;
        if (qtyDisplay) qtyDisplay.textContent = String(qty);
        if (totalDisplay) totalDisplay.textContent = `🪙 ${qty * price}`;
    }

    async function postForm(url, data) {
        const body = new FormData();
        Object.entries(data).forEach(([key, value]) => body.append(key, value));
        if (csrfToken) body.append('csrf_token', csrfToken);
        const response = await fetch(url, {
            method: 'POST',
            body,
            credentials: 'same-origin',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
            }
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
            throw new Error(payload.error || 'Request failed');
        }
        return payload.feedback || {};
    }

    function applyFeedback(feedback) {
        if (avgNode && typeof feedback.avg_rating === 'number') {
            avgNode.textContent = feedback.avg_rating.toFixed(1);
        }
        if (countNode && typeof feedback.rating_count === 'number') {
            countNode.textContent = String(feedback.rating_count);
        }
        if (likeNode && typeof feedback.like_count === 'number') {
            likeNode.textContent = String(feedback.like_count);
        }
        if (dislikeNode && typeof feedback.dislike_count === 'number') {
            dislikeNode.textContent = String(feedback.dislike_count);
        }
        if (reviewNode && typeof feedback.review_count === 'number') {
            reviewNode.textContent = String(feedback.review_count);
        }

        const userRating = Number(feedback.user_rating || 0);
        ratingButtons.forEach((button) => {
            const value = Number(button.dataset.rateValue || 0);
            button.classList.toggle('is-active', value <= userRating);
        });

        const userReaction = feedback.user_reaction || '';
        reactionButtons.forEach((button) => {
            button.classList.toggle('is-active', button.dataset.reactionAction === userReaction);
        });
    }

    if (quantityInput) {
        quantityInput.addEventListener('input', updateTotals);
        updateTotals();
    }

    if (shareButton && latInput && lngInput) {
        setLocationReady(Boolean(locationTextInput && locationTextInput.value));
        shareButton.addEventListener('click', function() {
            if (!navigator.geolocation) {
                setLocationReady(false, 'Geolocation is not supported. Type your location manually.');
                return;
            }
            if (!window.isSecureContext && location.protocol !== 'http:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
                setLocationReady(false, 'Location sharing needs HTTPS. Type your location manually.');
                return;
            }
            shareButton.disabled = true;
            shareButton.textContent = 'Sharing...';
            navigator.geolocation.getCurrentPosition(function(pos) {
                latInput.value = pos.coords.latitude;
                lngInput.value = pos.coords.longitude;
                if (locationTextInput) {
                    locationTextInput.value = `Lat ${pos.coords.latitude.toFixed(6)}, Lng ${pos.coords.longitude.toFixed(6)}`;
                }
                shareButton.textContent = 'Location Shared';
                shareButton.disabled = false;
                setLocationReady(true, 'Location shared successfully');
            }, function(error) {
                shareButton.disabled = false;
                shareButton.textContent = 'Share Location';
                const errorMessage = error && error.code === 1
                    ? 'Location permission denied. Type your location manually.'
                    : 'Could not get your location. Type your location manually.';
                setLocationReady(false, errorMessage);
            }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
        });
    }

    galleryButtons.forEach((button) => {
        button.addEventListener('click', function() {
            if (!mainProductImage) return;
            mainProductImage.src = this.dataset.imageSrc || '';
            galleryButtons.forEach((item) => item.classList.remove('is-active'));
            this.classList.add('is-active');
        });
    });

    ratingButtons.forEach((button) => {
        button.addEventListener('click', async function() {
            try {
                const feedback = await postForm(productPage.dataset.rateUrl, { rating: this.dataset.rateValue || '' });
                applyFeedback(feedback);
            } catch (error) {
                console.error(error);
            }
        });
    });

    reactionButtons.forEach((button) => {
        button.addEventListener('click', async function() {
            try {
                const feedback = await postForm(productPage.dataset.reactUrl, { reaction_type: this.dataset.reactionAction || '' });
                applyFeedback(feedback);
            } catch (error) {
                console.error(error);
            }
        });
    });

    applyFeedback({
        user_rating: Number(productPage.dataset.userRating || 0),
        user_reaction: productPage.dataset.userReaction || ''
    });
})();
