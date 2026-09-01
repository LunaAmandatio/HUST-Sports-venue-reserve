import os
import time
import tkinter as tk
from tkinter import messagebox

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


# 浏览器侧验证码识别（移植自 Tampermonkey 脚本：omggif + tesseract.js）。
# 用 execute_async_script 注入；arguments[-1] 是 Selenium 注入的回调。
CAPTCHA_JS = r"""
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
"""


class HustReserve:
    LOGIN_URL = (
        'https://pass.hust.edu.cn/cas/login'
        '?service=https%3A%2F%2Fpecg.hust.edu.cn%2Fcggl%2Findex1'
    )

    # name → (li_index, div_suffix, time_method_name)
    STADIUMS = {
        '光谷体育馆羽毛球场':         (1,  'div[2]', 'T2'),
        '光谷体育馆乒乓球场2':        (2,  'div[2]', 'T1'),
        '东区操场韵苑网球场2小时场':   (3,  'div',    'T4'),
        '东区操场匹克球场':           (4,  'div',    'T1'),
        '东区操场韵苑网球场1小时场':   (5,  'div',    'T1'),
        '中心区操场沁苑网球场2小时场': (6,  'div[2]', 'T4'),
        '中心区操场沙地网球场2小时场': (7,  'div[2]', 'T4'),
        '中心区操场沙地网球场1小时场': (8,  'div[2]', 'T1'),
        '中心区操场沁苑网球场1小时场': (9,  'div[2]', 'T1'),
        '西区操场西边网球场2小时场':   (10, 'div',    'T4'),
        '西区操场西边网球场1小时场':   (11, 'div',    'T1'),
        '西边羽毛球馆':               (12, 'div[2]', 'T2'),
        '西边体育馆乒乓球2':          (13, 'div[2]', 'T1'),
        '游泳馆游泳池':               (14, 'div[3]', 'T3'),
        '游泳馆羽毛球场':             (15, 'div[3]', 'T2'),
    }

    SLOTS_1H = (
        '08:00-09:00', '09:00-10:00', '10:00-11:00', '11:00-12:00',
        '12:00-13:00', '13:00-14:00', '14:00-15:00', '15:00-16:00',
        '16:00-17:00', '17:00-18:00', '18:00-19:00', '19:00-20:00',
        '20:00-21:00', '21:00-22:00',
    )
    SLOTS_2H = (
        '08:00-10:00', '10:00-12:00', '12:00-14:00',
        '14:00-16:00', '16:00-18:00', '18:00-20:00', '20:00-22:00',
    )
    SLOTS_SWIM = (
        '12:00-13:00', '13:00-14:00', '14:00-15:00', '15:00-16:00',
        '16:00-17:00', '17:00-18:00', '18:00-19:00', '19:00-20:00',
        '20:00-21:00',
    )
    SLOTS_MIXED = (
        '08:00-09:00', '09:00-10:00', '10:00-11:00', '11:00-12:00',
        '12:00-13:00', '13:00-14:00', '14:00-15:00', '15:00-16:00',
        '16:00-18:00', '18:00-20:00', '20:00-22:00',
    )

    def __init__(self, env_file='个人信息.env', headless=False):
        self.env_file = env_file
        self.headless = headless
        self.browser = None
        self.wait = None
        self.en_list = []
        self.manual_pwd = ''

    # ===== entry =====
    def run(self):
        root = tk.Tk()
        root.title('HUST抢场神器')
        root.geometry('500x500')
        tk.Label(
            root,
            text='这是HUST的场地检索小程序，请点击下方的按钮开始订场，订场时间为8点到22点',
            bg='green', fg='white', width=100, height=10,
        ).pack()

        def on_start():
            root.destroy()
            self.start()

        tk.Button(root, text='开始订场', width=20, height=5,
                  command=on_start, bg='red').place(x=170, y=250)
        tk.Label(root, text='本程序仅供交流学习使用，切勿用于非法用途',
                 width=100, height=10).pack(side='bottom')
        root.mainloop()

    # ===== login =====
    def start(self):
        load_dotenv(self.env_file)
        options = EdgeOptions()
        if self.headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1280,900')
            # 让 fetch/CORS 行为与有头一致；避免被识别为机器人
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument(
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 '
                'Edg/130.0.0.0'
            )
        self.browser = webdriver.Edge(options=options)
        self.browser.set_script_timeout(90)
        self.browser.get(self.LOGIN_URL)
        self.wait = WebDriverWait(self.browser, 10)

        account = os.getenv('account') or ''
        password = os.getenv('password') or ''

        while True:
            un = self.wait.until(EC.presence_of_element_located((By.ID, 'un')))
            pd = self.browser.find_element(By.ID, 'pd')
            un.clear(); un.send_keys(account)
            pd.clear(); pd.send_keys(password)

            self.fill_captcha()

            self.wait.until(EC.element_to_be_clickable((By.ID, 'index_login_btn'))).click()
            time.sleep(1.2)
            try:
                err = self.browser.find_element(By.ID, 'errormsg').text
            except NoSuchElementException:
                err = ''
            if not err:
                break
            if '验证码' in err:
                messagebox.showerror('验证码错误', '验证码识别错误，将自动刷新重试')
                continue
            messagebox.showerror('登录失败', err)
            continue

        self.name_choice()

    def fill_captcha(self, max_retries=3):
        """JS 注入自动识别。失败时刷新验证码重试，仍失败则弹窗手动。"""
        for attempt in range(max_retries):
            if attempt > 0:
                try:
                    self.browser.find_element(By.ID, 'codeImage').click()
                    time.sleep(0.8)
                except Exception:
                    pass
            try:
                result = self.browser.execute_async_script(CAPTCHA_JS)
            except Exception as e:
                print(f'[Captcha] 脚本异常 (尝试 {attempt + 1}): {e}')
                continue
            if isinstance(result, str) and len(result) == 4 and not result.startswith('ERR'):
                code_el = self.browser.find_element(By.ID, 'code')
                cur = code_el.get_attribute('value') or ''
                if cur != result:
                    code_el.clear()
                    code_el.send_keys(result)
                print(f'[Captcha] 自动识别: {result}')
                return True
            print(f'[Captcha] 失败 (尝试 {attempt + 1}/{max_retries}): {result}')

        self.manual_captcha()
        el = self.browser.find_element(By.ID, 'code')
        el.clear()
        el.send_keys(self.manual_pwd)
        return False

    def manual_captcha(self):
        win = tk.Tk()
        win.title('验证码输入')
        win.geometry('320x110')
        pwd_var = tk.StringVar()
        result = tk.Label(win, bg='red', width=100, height=1)
        pwd_var.trace('w', lambda *a: result.configure(text=f'输入的验证码：{pwd_var.get()}'))

        def submit():
            self.manual_pwd = pwd_var.get()
            win.destroy()

        tk.Label(win, text='自动识别失败，请手动输入', bg='red', width=30, height=1).pack()
        entry = tk.Entry(win, width=50, textvariable=pwd_var)
        entry.pack()
        entry.focus_set()
        result.pack()
        tk.Button(win, text='完成', command=submit).pack()
        win.mainloop()

    # ===== stadium =====
    def name_choice(self):
        win = tk.Tk()
        win.title('选择体育馆')
        win.geometry('400x320')
        lb = tk.Listbox(win, width=100, height=20)
        for name in self.STADIUMS:
            lb.insert(tk.END, name)

        def enter():
            try:
                name = lb.get(lb.curselection()[0])
            except (IndexError, tk.TclError):
                return
            win.destroy()
            self.stadium_choice(name)

        tk.Button(win, text='确定', command=enter).pack()
        lb.pack(side='left', fill='y')
        win.mainloop()

    def stadium_choice(self, name):
        li_idx, div_suffix, method = self.STADIUMS[name]
        xpath = f'/html/body/div[2]/div[2]/ul/li[{li_idx}]/div[1]/{div_suffix}/span/a'
        el = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        el.click()
        getattr(self, method)()

    # ===== day =====
    def day_choice(self):
        win = tk.Tk()
        win.title('日期选择')
        win.geometry('200x300')
        picked = []

        def pick(n):
            picked.append(n)
            win.destroy()

        tk.Button(win, text='今天', width=10, height=4, bg='red', fg='white',
                  command=lambda: pick(0)).pack(pady=5)
        tk.Button(win, text='明天', width=10, height=4, bg='red', fg='white',
                  command=lambda: pick(1)).pack(pady=5)
        tk.Button(win, text='后天', width=10, height=4, bg='red', fg='white',
                  command=lambda: pick(2)).pack(pady=5)
        win.mainloop()

        n = picked[0] if picked else 0
        for _ in range(n):
            btn = self.browser.find_element(
                By.XPATH, '/html/body/div[2]/div[2]/div[2]/div[1]/div[3]')
            self.browser.execute_script('arguments[0].click();', btn)
            time.sleep(0.2)

    # ===== time selection =====
    def _select_range(self, slots, slot_to_hour, on_done):
        """两步选择：开始时间 → 结束时间 → 回调 on_done(start_h, end_h)。"""
        result = {}

        def make_listbox_window(title, key):
            win = tk.Tk()
            win.title(title)
            win.geometry('300x320')
            lb = tk.Listbox(win, width=100, height=50)
            for s in slots:
                lb.insert(tk.END, s)

            def confirm():
                try:
                    slot = lb.get(lb.curselection()[0])
                except (IndexError, tk.TclError):
                    return
                result[key] = slot_to_hour[slot]
                win.destroy()

            tk.Button(win, text=f'{title}确定', command=confirm,
                      width=20, height=2).pack(side='bottom')
            lb.pack(side='left', fill='y')
            win.mainloop()

        make_listbox_window('开始时间', 'start')
        if 'start' not in result:
            return
        make_listbox_window('结束时间', 'end')
        if 'end' not in result:
            return

        s, e = result['start'], result['end']
        if s > e:
            messagebox.showinfo('出错', '结束时间早于开始时间')
            return
        on_done(s, e)

    def T1(self):
        """1 小时整点场。"""
        self.day_choice()
        mapping = {slot: i + 8 for i, slot in enumerate(self.SLOTS_1H)}

        def handle(s, e):
            self.en_list.clear()
            for h in range(s, e + 1):
                self._time_choice(f'{h:02d}:00:00')
            self.scenes()

        self._select_range(self.SLOTS_1H, mapping, handle)

    def T2(self):
        """2 小时整点场（羽毛球类）。"""
        self.day_choice()
        mapping = {slot: 2 * i + 8 for i, slot in enumerate(self.SLOTS_2H)}

        def handle(s, e):
            self.en_list.clear()
            for h in range(s, e + 1, 2):
                self._time_choice(f'{h:02d}:00:00')
            self.scenes()

        self._select_range(self.SLOTS_2H, mapping, handle)

    def T3(self):
        """游泳馆 1 小时场，12 点起。"""
        self.day_choice()
        mapping = {slot: i + 12 for i, slot in enumerate(self.SLOTS_SWIM)}

        def handle(s, e):
            self.en_list.clear()
            for h in range(s, e + 1):
                self._time_choice(f'{h:02d}:00:00')
            self.scenes()

        self._select_range(self.SLOTS_SWIM, mapping, handle)

    def T4(self):
        """混合：8–15 为 1 小时，16/18/20 起为 2 小时。"""
        self.day_choice()
        mapping = {}
        for i, slot in enumerate(self.SLOTS_MIXED):
            mapping[slot] = (i + 8) if i < 8 else (2 * (i - 8) + 16)

        def handle(s, e):
            self.en_list.clear()
            if s <= 15:
                for h in range(s, min(e, 15) + 1):
                    self._time_choice(f'{h:02d}:00:00')
            if e >= 16:
                start_2h = max(s, 16)
                if start_2h % 2:
                    start_2h += 1
                for h in range(start_2h, e + 1, 2):
                    self._time_choice(f'{h:02d}:00:00')
            self.scenes()

        self._select_range(self.SLOTS_MIXED, mapping, handle)

    def _time_choice(self, ti):
        try:
            Select(self.browser.find_element(By.ID, 'starttime')).select_by_value(ti)
        except Exception as e:
            print(f'[Time] 选 {ti} 失败: {e}')
            return
        for idx, sta in enumerate(self.browser.find_elements(By.CLASS_NAME, 'spacezt'), 1):
            self.en_list.append({
                'time': ti,
                'idx': idx,
                'available': sta.text == '可预约',
            })

    # ===== booking =====
    def scenes(self):
        if not self.en_list:
            messagebox.showinfo('提示', '没有可显示的场地')
            return
        win = tk.Tk()
        win.title('场地状态')
        win.geometry('420x500')
        lines = [
            f"在{item['time']}的第{item['idx']}场地{'可预约' if item['available'] else '无法预约'}"
            for item in self.en_list
        ]
        lb = tk.Listbox(win, width=100, height=50)
        for s in lines:
            lb.insert(tk.END, s)

        def book():
            try:
                sel = lb.curselection()[0]
            except (IndexError, tk.TclError):
                return
            item = self.en_list[sel]
            if not item['available']:
                messagebox.showerror('错误', '请预约可预约的场地')
                return
            if not messagebox.askyesno('提示', '是否确认预定该场地'):
                return
            try:
                Select(self.browser.find_element(By.ID, 'starttime')).select_by_value(item['time'])
                states = self.browser.find_elements(By.CLASS_NAME, 'spacezt')
                self.browser.execute_script('arguments[0].click();', states[item['idx'] - 1])
                try:
                    enter = self.browser.find_element(By.ID, 'partnerCardType1')
                    self.browser.execute_script('arguments[0].click();', enter)
                except NoSuchElementException:
                    pass
                confirm = self.browser.find_element(
                    By.XPATH, '//*[@id="command"]/div[3]/input[3]')
                self.browser.execute_script('arguments[0].click();', confirm)
                try:
                    decision = self.browser.find_element(
                        By.XPATH, '//*[@id="dialog_1"]/div[2]/div/div/div[2]/div')
                    self.browser.execute_script('arguments[0].click();', decision)
                except NoSuchElementException:
                    pass
                messagebox.showinfo('提示', '预约成功，请付款')
            except Exception as e:
                messagebox.showerror('错误', f'预约失败：{e}')

        tk.Button(win, text='预约场地确定', command=book, width=20, height=2).pack(side='bottom')
        lb.pack(side='left', fill='y')
        win.mainloop()


if __name__ == '__main__':
    HustReserve().run()
