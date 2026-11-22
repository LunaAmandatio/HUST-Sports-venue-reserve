// Selenium 注入的最后一个参数用于把验证码识别结果返回给 Python。
const callback = arguments[arguments.length - 1];
(async () => {
    const loadScript = (src) => new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = () => reject(new Error('load ' + src));
        document.head.appendChild(s);
    });
    try {
        if (typeof Tesseract === 'undefined') {
            await loadScript('https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js');
        }
        if (typeof GifReader === 'undefined') {
            await loadScript('https://cdn.jsdelivr.net/npm/omggif@1.0.10/omggif.min.js');
        }

        const img = document.querySelector('#codeImage');
        const input = document.querySelector('#code');
        if (!img || !input) return callback('ERR:no img/input');

        // 用原始 src 抓 GIF（仅当当前 src 已经是 blob 时，跳过抓取直接用 img）。
        let gifData;
        if (img.src && img.src.startsWith('blob:')) {
            const resp = await fetch(img.src);
            gifData = await resp.arrayBuffer();
        } else {
            const resp = await fetch(img.src);
            if (!resp.ok) return callback('ERR:fetch ' + resp.status);
            gifData = await resp.arrayBuffer();
            // 立刻把 img.src 替换成本地 blob，防止页面再次请求 /code
            // 触发服务器重新生成验证码导致 OCR 结果失效（这是原 Tampermonkey 脚本的关键步骤）。
            try {
                if (window.__hustBlobUrl) URL.revokeObjectURL(window.__hustBlobUrl);
                const blob = new Blob([gifData], {type: 'image/gif'});
                const blobUrl = URL.createObjectURL(blob);
                window.__hustBlobUrl = blobUrl;
                img.src = blobUrl;
            } catch (e) { /* 即使失败也继续 OCR */ }
        }

        const reader = new GifReader(new Uint8Array(gifData));
        const numFrames = reader.numFrames();
        const W = reader.width, H = reader.height;
        const framesToExtract = 4;
        const selectedIdx = [];
        for (let i = 0; i < numFrames && selectedIdx.length < framesToExtract; i++) {
            selectedIdx.push(i);
        }
        if (selectedIdx.length === 0) return callback('ERR:no frames');

        const FG = 0, BG = 255, bgT = 254;
        const processFrame = (canvas) => {
            const ctx = canvas.getContext('2d', {willReadFrequently: true});
            const w = canvas.width, h = canvas.height;
            const imageData = ctx.getImageData(0, 0, w, h);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4) {
                data[i + 3] = 255;
                if (data[i] > bgT && data[i + 1] > bgT && data[i + 2] > bgT) {
                    data[i] = data[i + 1] = data[i + 2] = BG;
                } else {
                    data[i] = data[i + 1] = data[i + 2] = FG;
                }
            }
            const getPx = (d, x, y) => (x < 0 || x >= w || y < 0 || y >= h) ? BG : d[(y * w + x) * 4];
            const setPx = (d, x, y, v) => {
                const i = (y * w + x) * 4;
                d[i] = d[i + 1] = d[i + 2] = v;
                d[i + 3] = 255;
            };
            const orig = new Uint8ClampedArray(data);
            const proc = new Uint8ClampedArray(data);
            for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
                let boundary = false;
                for (let ny = -1; ny <= 0 && !boundary; ny++) {
                    for (let nx = -1; nx <= 0; nx++) {
                        if (getPx(orig, x + nx, y + ny) === BG) { boundary = true; break; }
                    }
                }
                setPx(proc, x, y, boundary ? BG : FG);
            }
            const eroded = new Uint8ClampedArray(proc);
            for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
                let hasFg = false;
                for (let ny = -1; ny <= 0 && !hasFg; ny++) {
                    for (let nx = -1; nx <= 0; nx++) {
                        if (getPx(eroded, x + nx, y + ny) === FG) { hasFg = true; break; }
                    }
                }
                setPx(proc, x, y, hasFg ? FG : getPx(eroded, x, y));
            }
            imageData.data.set(proc);
            return imageData;
        };

        const frameCanvas = document.createElement('canvas');
        frameCanvas.width = W;
        frameCanvas.height = H;
        const frameCtx = frameCanvas.getContext('2d', {willReadFrequently: true});
        const processed = [];
        for (const idx of selectedIdx) {
            try {
                const info = reader.frameInfo(idx);
                if (frameCanvas.width !== info.width || frameCanvas.height !== info.height) {
                    frameCanvas.width = info.width;
                    frameCanvas.height = info.height;
                }
                const fid = frameCtx.createImageData(frameCanvas.width, frameCanvas.height);
                reader.decodeAndBlitFrameRGBA(idx, fid.data);
                frameCtx.putImageData(fid, 0, 0);
                processed.push(processFrame(frameCanvas));
            } catch (e) { /* skip bad frame */ }
        }
        if (processed.length === 0) return callback('ERR:no processed');

        const merged = document.createElement('canvas');
        merged.width = W;
        merged.height = H;
        const mCtx = merged.getContext('2d');
        const mid = mCtx.createImageData(W, H);
        const md = mid.data;
        for (let i = 0; i < md.length; i += 4) {
            md[i] = md[i + 1] = md[i + 2] = 255;
            md[i + 3] = 255;
        }
        processed.forEach(fd => {
            const d = fd.data;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i] === 0) { md[i] = md[i + 1] = md[i + 2] = 0; }
            }
        });
        mCtx.putImageData(mid, 0, 0);

        const worker = await Tesseract.createWorker('eng', 1);
        await worker.setParameters({
            tessedit_char_whitelist: '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
            tessedit_pageseg_mode: Tesseract.PSM.SINGLE_LINE,
        });
        const {data: {text}} = await worker.recognize(merged);
        await worker.terminate();

        const code = text.replace(/[^a-zA-Z0-9]/g, '');
        if (code.length === 4) {
            input.value = code;
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            callback(code);
        } else {
            callback('ERR:len ' + code.length);
        }
    } catch (e) {
        callback('ERR:' + (e && e.message ? e.message : String(e)));
    }
})();
