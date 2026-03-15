/**
 * Set-of-Marks DOM Extraction Script — Universal Edition
 *
 * Design goals:
 *  - Works on any site regardless of CSS class naming (no class-based heuristics)
 *  - Single extraction core shared by drawAgentMarkers + extractPageStateJSON
 *  - `near` context for every ambiguous button, not just a hardcoded list
 *  - Filters useless noise (empty inputs, invisible elements, agent's own markers)
 *  - Multi-currency price detection (₽ $ € £ ¥ and plain numbers)
 */

// ─── Selectors ────────────────────────────────────────────────────────────────

window.INTERACTIVE_SELECTORS = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="menuitem"]',
    '[role="checkbox"]', '[role="radio"]', '[role="switch"]', '[role="combobox"]',
    '[role="option"]', '[onclick]', '[tabindex]:not([tabindex="-1"])',
    'summary', '[contenteditable="true"]',
];

// Buttons whose own text is too generic to be meaningful without surrounding context.
const AMBIGUOUS_BUTTON_RE = /^(\+|-|×|✕|✓|▶|◀|▲|▼|увеличить|уменьшить|добавить|удалить|в корзину|купить|add|remove|buy|delete|edit|more|less|expand|collapse|toggle|show|hide|open|close|like|share|save|submit|go|ok|yes|no|cancel)$/i;

// ─── Visibility ───────────────────────────────────────────────────────────────

window.isVisible = function (el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight + 100) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) < 0.1) return false;
    return true;
};

// ─── Context extraction ───────────────────────────────────────────────────────

/**
 * Find the nearest ancestor that looks like a self-contained content block
 * (product card, form row, list item, article snippet, etc.).
 *
 * Strategy: structure-based, not class-based.
 * A content block is an ancestor that has meaningful text and is compact enough
 * to be a single item (< 400 chars), not the whole section/page.
 */
window.getNearestBlock = function (el) {
    const PRICE_RE = /\d[\d\s]*[₽$€£¥]|[₽$€£¥]\s*\d|[\d,.]+\s*(руб|usd|eur|rub)/i;
    const NAME_RE = /[а-яёА-ЯЁa-zA-Z]{3,}/;

    let node = el.parentElement;
    let depth = 0;
    let bestWithPrice = null;   // smallest ancestor with price+name
    let bestAny = null;         // smallest ancestor with just name

    while (node && node !== document.body && depth < 10) {
        const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
        if (text.length >= 15 && NAME_RE.test(text)) {
            if (PRICE_RE.test(text)) {
                // Take the SMALLEST ancestor that has price — stop immediately
                if (!bestWithPrice) bestWithPrice = node;
                // If it's compact enough — perfect match, stop walking
                if (text.length < 600) return node;
            }
            if (!bestAny && text.length < 600) bestAny = node;
        }
        node = node.parentElement;
        depth++;
    }
    // Prefer price block even if large, then fallback to any named block
    return bestWithPrice || bestAny;
};

/**
 * Extract a short human-readable label from a content block.
 * Returns "Product name PRICE" or just "Product name".
 */
window.getBlockLabel = function (node) {
    if (!node) return '';

    const PRICE_RE = /\d[\d\s]*[₽$€£¥]|[₽$€£¥]\s*\d[\d\s,.]*/;
    const text = node.innerText || node.textContent || '';
    const lines = text.split('\n')
        .map(l => l.replace(/\s+/g, ' ').trim())
        .filter(l => l.length > 2 && l.length < 100);

    // Prefer shorter lines as product name — less likely to be a description blob
    const nameLines = lines.filter(l => /[а-яёА-ЯЁa-zA-Z]{3,}/.test(l) && !/^\d+$/.test(l));
    const name = nameLines.sort((a, b) => a.length - b.length)[0] || '';

    const priceMatch = text.match(PRICE_RE);
    const price = priceMatch ? priceMatch[0].trim() : '';

    return [name, price].filter(Boolean).join(' ').substring(0, 70);
};

// ─── Element text ─────────────────────────────────────────────────────────────

window.getElementText = function (el) {
    const tag = el.tagName.toUpperCase();

    // 1. Explicit accessibility labels always win
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim().substring(0, 80);

    const ariaLabelledBy = el.getAttribute('aria-labelledby');
    if (ariaLabelledBy) {
        const labelEl = document.getElementById(ariaLabelledBy);
        if (labelEl) return labelEl.textContent.trim().substring(0, 80);
    }

    // 2. Form elements: prefer associated <label>
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        if (el.id) {
            try {
                const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (label) return label.textContent.trim().substring(0, 80);
            } catch (_) { }
        }
        const wrapLabel = el.closest('label');
        if (wrapLabel) {
            const clone = wrapLabel.cloneNode(true);
            clone.querySelectorAll('input,textarea,select').forEach(c => c.remove());
            const t = clone.textContent.trim();
            if (t) return t.substring(0, 80);
        }
        if (tag === 'INPUT' || tag === 'TEXTAREA') {
            if (el.placeholder) return el.placeholder.substring(0, 80);
            if (el.name) return el.name.substring(0, 60);
        }
        if (tag === 'SELECT') {
            return el.options[el.selectedIndex]?.text || el.name || '';
        }
        return '';
    }

    // 3. Own visible text
    const ownText = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (ownText.length > 0 && ownText.length <= 40) return ownText;
    if (ownText.length > 40) return ownText.substring(0, 40);

    return '';
};

// ─── Element type ─────────────────────────────────────────────────────────────

window.getElementType = function (el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textarea';
    if (tag === 'input') return el.type || 'input';
    if (tag === 'button' || el.getAttribute('role') === 'button') return 'button';
    const role = el.getAttribute('role');
    if (role) return role;
    return 'interactive';
};

// ─── Extra metadata ───────────────────────────────────────────────────────────

window.getExtraInfo = function (el) {
    const info = {};
    const tag = el.tagName.toLowerCase();

    if (tag === 'a' && el.href) {
        try {
            const url = new URL(el.href);
            info.href = (url.origin === window.location.origin)
                ? url.pathname.substring(0, 60)
                : el.href.substring(0, 60);
        } catch (_) {
            info.href = el.href.substring(0, 60);
        }
    }

    if (el.disabled) info.disabled = true;

    const testId = el.getAttribute('data-testid')
        || el.getAttribute('data-test-id')
        || el.getAttribute('data-qa')
        || el.getAttribute('data-cy');
    if (testId) info.testid = testId.substring(0, 50);

    const title = el.getAttribute('title');
    if (title && title.trim()) info.title = title.trim().substring(0, 40);

    if (tag === 'input' && el.value && el.value.length > 0 && el.value.length < 50) {
        info.value = el.value;
    }
    if (tag === 'select' && el.selectedIndex >= 0) {
        info.value = el.options[el.selectedIndex]?.text || '';
    }

    const expanded = el.getAttribute('aria-expanded');
    if (expanded !== null) info.expanded = expanded === 'true';

    const ariaChecked = el.getAttribute('aria-checked');
    const checked = ariaChecked !== null ? ariaChecked : (el.type === 'checkbox' ? String(el.checked) : null);
    if (checked !== null) info.checked = checked;

    return Object.keys(info).length > 0 ? info : undefined;
};

// ─── Core extraction (single source of truth) ─────────────────────────────────

/**
 * Used by both drawAgentMarkers and extractPageStateJSON.
 * Returns array of element descriptors: { id, type, text, near, extra }
 */
window._extractElements = function () {
    const all = document.querySelectorAll(window.INTERACTIVE_SELECTORS.join(', '));
    const results = [];
    let idCounter = 1;

    for (const el of all) {
        if (!window.isVisible(el)) continue;
        if (el.closest('#agent-markers-container')) continue;

        const etype = window.getElementType(el);
        const text = window.getElementText(el);

        // Drop empty counter/quantity inputs — pure noise
        if ((etype === 'text' || etype === 'number') && !text.trim()) continue;

        const id = idCounter++;
        el.setAttribute('data-agent-id', String(id));

        // Attach surrounding context for any button with ambiguous/short text
        let near = '';
        if (etype === 'button') {
            const trimmed = text.trim();
            if (trimmed.length <= 3 || AMBIGUOUS_BUTTON_RE.test(trimmed)) {
                const block = window.getNearestBlock(el);
                near = window.getBlockLabel(block);
            }
        }

        results.push({
            id,
            type: etype,
            text,
            near,
            extra: window.getExtraInfo(el),
        });
    }

    return results;
};

// ─── Marker overlay ───────────────────────────────────────────────────────────

window.clearAgentMarkers = function () {
    const container = document.getElementById('agent-markers-container');
    if (container) container.remove();
    document.querySelectorAll('[data-agent-id]').forEach(el => el.removeAttribute('data-agent-id'));
};

window.drawAgentMarkers = function () {
    window.clearAgentMarkers();

    const container = document.createElement('div');
    container.id = 'agent-markers-container';
    container.style = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2147483647;';
    document.body.appendChild(container);

    const fragment = document.createDocumentFragment();
    const elements = window._extractElements();

    for (const descriptor of elements) {
        const el = document.querySelector(`[data-agent-id="${descriptor.id}"]`);
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        const label = document.createElement('div');
        label.textContent = descriptor.id;
        label.style = `position:fixed;left:${rect.left}px;top:${rect.top}px;background:red;color:white;font-size:12px;font-weight:bold;padding:1px 4px;border-radius:3px;box-shadow:0 0 4px black;pointer-events:none;`;
        fragment.appendChild(label);
    }

    container.appendChild(fragment);
    return elements;
};

// ─── JSON state export ────────────────────────────────────────────────────────

window.extractPageStateJSON = function () {
    const elements = window._extractElements();
    return JSON.stringify({
        title: document.title,
        url: window.location.href,
        elements,
        page_text: window.getViewportText(2000),
    });
};

// ─── Page text ────────────────────────────────────────────────────────────────

/**
 * Extract relevant page text by walking ancestors of interactive elements.
 * Gives product names, prices, labels and confirmation messages
 * instead of nav bars and cookie banners from raw body.innerText.
 */
window.getViewportText = function (maxChars) {
    maxChars = maxChars || 2000;
    const seen = new Set();
    const parts = [];
    let total = 0;

    const elements = document.querySelectorAll(window.INTERACTIVE_SELECTORS.join(', '));
    for (const el of elements) {
        if (!window.isVisible(el)) continue;

        let node = el.parentElement;
        let depth = 0;
        while (node && depth < 6) {
            const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
            if (text.length >= 30 && text.length <= 400) {
                const key = text.substring(0, 100);
                if (!seen.has(key)) {
                    seen.add(key);
                    parts.push(text);
                    total += text.length + 1;
                    if (total >= maxChars) break;
                }
                break;
            }
            node = node.parentElement;
            depth++;
        }
        if (total >= maxChars) break;
    }

    // Fallback for simple pages with few interactive elements
    if (total < 200) {
        const bodyText = ((document.body && document.body.innerText) || '')
            .replace(/\s+/g, ' ').trim()
            .substring(0, maxChars - total);
        if (bodyText) parts.push(bodyText);
    }

    return parts.join('\n').substring(0, maxChars);
};