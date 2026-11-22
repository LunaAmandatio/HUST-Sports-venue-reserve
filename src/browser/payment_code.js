// 只识别明确带有支付提示的元素，不截取整个网页或任意二维码。
const paymentLabel = /扫码\s*(?:支付|付款)|(?:支付|付款|收款)\s*(?:用)?\s*二维码|二维码\s*(?:支付|付款)/;
const qrHint = /qr[-_\s]?code|二维码/i;
const paymentHint = /支付|付款|收款|wechatpay|alipay|payment|pay[-_\s]?qr/i;
const metadata = node => [node.id, node.getAttribute('class'), node.getAttribute('alt'),
    node.getAttribute('aria-label'), node.getAttribute('title')].filter(Boolean).join(' ');
return Array.from(document.querySelectorAll('img, canvas, svg')).filter(node => {
    const box = node.getBoundingClientRect();
    const css = getComputedStyle(node);
    if (box.width < 96 || box.width > 640 || Math.abs(box.width - box.height) > box.width * 0.12
        || css.visibility !== 'visible' || !node.getClientRects().length || css.opacity === '0') return false;
    if (node instanceof HTMLImageElement && (!node.complete || node.naturalWidth < 64)) return false;

    const label = metadata(node) + ' ' + metadata(node.parentElement);
    if (paymentLabel.test(label) || (qrHint.test(label) && paymentHint.test(label))) return true;

    let region = node.parentElement;
    for (let level = 0; level < 3 && region && region !== document.body; level++, region = region.parentElement) {
        const text = (region.innerText || '').trim();
        if (text.length > 350 || !paymentLabel.test(text)) continue;
        if (qrHint.test(label) || region.querySelectorAll('img, canvas, svg').length === 1) return true;
    }
    return false;
});
