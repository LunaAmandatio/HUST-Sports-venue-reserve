import argparse
import os
import re
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from urllib.parse import urlsplit

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException, StaleElementReferenceException, TimeoutException, WebDriverException,
)
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


class ReserveError(Exception):
    pass


class HustReserve:
    CAS_HOST = 'pass.hust.edu.cn'
    VENUE_HOST = 'pecg.hust.edu.cn'
    VENUE_URL = 'https://pecg.hust.edu.cn/cggl/index1'
    VENUE_LINKS = (By.XPATH, '/html/body/div[2]/div[2]/ul/li//a')
    NEXT_DAY = (By.XPATH, '/html/body/div[2]/div[2]/div[2]/div[1]/div[3]')
    MFA_MESSAGE = re.compile(r'动态验证码|二次认证|双因子|验证码.*(?:企业微信|手机)|(?:企业微信|手机).*验证码')
    LOGIN_URL = (
        'https://pass.hust.edu.cn/cas/login'
        '?service=https%3A%2F%2Fpecg.hust.edu.cn%2Fcggl%2Findex1'
    )

    # name → (li_index, div_suffix)
    STADIUMS = {
        '光谷体育馆羽毛球场':         (1,  'div[2]'),
        '光谷体育馆乒乓球场2':        (2,  'div[2]'),
        '东区操场韵苑网球场2小时场':   (3,  'div'),
        '东区操场匹克球场':           (4,  'div'),
        '东区操场韵苑网球场1小时场':   (5,  'div'),
        '中心区操场沁苑网球场2小时场': (6,  'div[2]'),
        '中心区操场沙地网球场2小时场': (7,  'div[2]'),
        '中心区操场沙地网球场1小时场': (8,  'div[2]'),
        '中心区操场沁苑网球场1小时场': (9,  'div[2]'),
        '西区操场西边网球场2小时场':   (10, 'div'),
        '西区操场西边网球场1小时场':   (11, 'div'),
        '西边羽毛球馆':               (12, 'div[2]'),
        '西边体育馆乒乓球2':          (13, 'div[2]'),
        '游泳馆游泳池':               (14, 'div[3]'),
        '游泳馆羽毛球场':             (15, 'div[3]'),
    }

    def __init__(self, env_file='.env', headless=False, manual_captcha=False):
        self.env_file = env_file
        self.headless = headless
        self.browser = None
        self.wait = None
        self.en_list = []
        self.force_manual_captcha = manual_captcha

    # ===== entry =====
    def run(self):
        root = tk.Tk()
        root.title('HUST 场地查询')
        root.geometry('500x500')
        tk.Label(
            root,
            text='登录后可按场馆、日期和时段查询场地状态',
            bg='green', fg='white', width=100, height=10,
        ).pack()

        def on_start():
            root.destroy()
            self.start()

        tk.Button(root, text='登录并查询', width=20, height=5,
                  command=on_start, bg='red').place(x=170, y=250)
        tk.Label(root, text='本程序仅供交流学习使用，切勿用于非法用途',
                 width=100, height=10).pack(side='bottom')
        root.mainloop()

    # ===== login =====
    def start(self):
        load_dotenv(self.env_file, interpolate=False)
        account = (os.getenv('account') or '').strip()
        password = os.getenv('password') or ''
        if not account or not password:
            messagebox.showerror('缺少登录配置', '请在 .env 或 --env-file 指定的文件中填写 account 和 password。')
            return
        options = EdgeOptions()
        if self.headless:
            options.add_argument('--headless=new')
            options.add_argument('--window-size=1280,900')
        try:
            self.browser = webdriver.Edge(options=options)
            self.browser.set_script_timeout(45)
            self.browser.set_page_load_timeout(30)
            self.wait = WebDriverWait(self.browser, 12, poll_frequency=0.25,
                                      ignored_exceptions=(StaleElementReferenceException,))
            logged_in = self.login(account, password)
            del password
            if not logged_in:
                return
            while name := self.name_choice():
                self.stadium_choice(name)
                self._open_venue_index()
        except ReserveError as error:
            messagebox.showerror('操作未完成', str(error))
        except TimeoutException:
            messagebox.showerror('页面超时', '未确认登录或场馆页面加载完成，请检查网络后重试。')
        except WebDriverException:
            messagebox.showerror('浏览器错误', '请确认 Microsoft Edge 和 WebDriver 可用，并重新运行程序。')
        finally:
            if self.browser is not None:
                try:
                    self.browser.quit()
                except WebDriverException:
                    pass
                self.browser = None

    def _visible(self, element_id):
        return next((element for element in self.browser.find_elements(By.ID, element_id)
                     if element.is_displayed()), None)

    def _is_cas(self):
        url = urlsplit(self.browser.current_url)
        return url.scheme == 'https' and url.netloc == self.CAS_HOST and url.path.startswith('/cas/')

    def _is_venue(self):
        url = urlsplit(self.browser.current_url)
        return (url.scheme == 'https' and url.netloc == self.VENUE_HOST
                and url.path.startswith('/cggl/'))

    def _open_venue_index(self):
        self.browser.get(self.VENUE_URL)

        def ready(_):
            if not self._is_venue():
                raise ReserveError('未能通过 HTTPS 打开已登录的场地系统，请在学校浏览器页面核对后重试。')
            return self.browser.find_elements(*self.VENUE_LINKS)

        self.wait.until(ready)

    def _login_state(self):
        if self._is_venue():
            return ('ready',) if self.browser.find_elements(*self.VENUE_LINKS) else None
        if not self._is_cas():
            url = urlsplit(self.browser.current_url)
            if url.scheme == 'http' and url.netloc == self.VENUE_HOST:
                return ('venue_redirect',)
            raise ReserveError('登录跳转到了未知地址，已停止。')
        error_el = self._visible('errormsg')
        error = error_el.text.strip() if error_el else ''
        phone = self._visible('phoneCode')
        if phone:
            return ('mfa', phone.id, error)
        if self.MFA_MESSAGE.search(error):
            return None
        captcha, image = self._visible('code'), self._visible('codeImage')
        if captcha and image and (not error or '验证码' in error):
            return ('captcha', captcha.id, image.get_attribute('src'), error)
        if error:
            return ('rejected',)
        username = self._visible('un')
        if not captcha and username and self._visible('pd') and self._visible('index_login_btn'):
            return ('form', username.id)
        return None

    @staticmethod
    def _fill(element, value):
        if element.get_property('value') != value:
            element.clear()
            element.send_keys(value)

    def login(self, account, password):
        self.browser.get(self.LOGIN_URL)
        state = self.wait.until(lambda _: self._login_state())
        retries = {'captcha': 0, 'mfa': 0}
        submitted = False
        while state[0] != 'ready':
            kind = state[0]
            if kind == 'venue_redirect':
                self._open_venue_index()
                return True
            if kind == 'rejected':
                raise ReserveError('学校拒绝登录，请核对账号密码或在学校页面查看提示。')
            if kind in retries:
                retries[kind] += 1
                if retries[kind] > 3:
                    raise ReserveError('验证码尝试次数已达上限，请检查后重新登录。')
                if kind == 'mfa':
                    prompt = ('动态码未通过，请输入有效的新码。' if retries[kind] > 1
                              else '学校要求双因子认证，请输入学校发送的动态码。')
                    code = simpledialog.askstring('双因子认证', prompt, show='*')
                else:
                    code = None
                    password_field = self._visible('pd')
                    if not submitted and not self.force_manual_captcha:
                        if not password_field or not password_field.get_property('value'):
                            try:
                                result = self.browser.execute_async_script(CAPTCHA_JS)
                                if isinstance(result, str) and re.fullmatch(r'[a-zA-Z0-9]{4}', result):
                                    code = result
                            except WebDriverException:
                                pass
                    if code is not None:
                        updated = self._login_state()
                        if not updated or updated[:2] != state[:2]:
                            raise ReserveError('学校验证码图片已失效，请重新登录。')
                        state = updated
                    else:
                        image = self._visible('codeImage')
                        if not image:
                            raise ReserveError('学校验证码图片已失效，请重新登录。')
                        state = self._login_state()
                        if not state or state[0] != 'captcha':
                            raise ReserveError('学校验证码图片已失效，请重新登录。')
                        code = self.manual_captcha(image.screenshot_as_base64)
                if not code or not code.strip():
                    return False
                current = self._login_state()
                if current != state:
                    raise ReserveError('学校验证页面已变化，请重新登录。')
                self._fill(self._visible('phoneCode' if kind == 'mfa' else 'code'), code.strip())
            if not self._is_cas():
                raise ReserveError('登录页面已变化，未提交凭据。')
            for element_id, value in (('un', account), ('pd', password)):
                field = self._visible(element_id)
                if field:
                    self._fill(field, value)
            before = self._login_state()
            if kind == 'form' and before and before[0] in retries:
                state = before
                continue
            if not before or before[0] != kind:
                raise ReserveError('学校登录表单已变化，请重新登录。')
            self.wait.until(EC.element_to_be_clickable((By.ID, 'index_login_btn'))).click()
            submitted = True

            def outcome(_):
                after = self._login_state()
                return after if after and after[0] != 'form' and after != before else None

            state = self.wait.until(outcome)
        return True

    def manual_captcha(self, image_data):
        win = tk.Tk()
        win.title('图形验证码')
        result = []
        image = tk.PhotoImage(master=win, data=image_data, format='png')
        tk.Label(win, text='请输入图片中的验证码').pack(padx=20, pady=6)
        tk.Label(win, image=image).pack(pady=4)
        entry = tk.Entry(win, width=16)
        entry.pack(pady=4)
        entry.focus_set()

        def submit():
            if entry.get().strip():
                result.append(entry.get().strip())
                win.destroy()

        tk.Button(win, text='提交', command=submit).pack(pady=6)
        win.bind('<Return>', lambda _: submit())
        win.mainloop()
        return result[0] if result else None

    # ===== stadium =====
    def name_choice(self):
        win = tk.Tk()
        win.title('选择体育馆（关闭窗口退出）')
        picked = []
        lb = tk.Listbox(win, width=44, height=len(self.STADIUMS))
        for name in self.STADIUMS:
            lb.insert(tk.END, name)

        def enter():
            try:
                picked.append(lb.get(lb.curselection()[0]))
            except (IndexError, tk.TclError):
                return
            win.destroy()

        tk.Button(win, text='确定', command=enter).pack()
        lb.pack(side='left', fill='y')
        win.mainloop()
        return picked[0] if picked else None

    def stadium_choice(self, name):
        if not self._is_venue():
            raise ReserveError('场地登录会话已失效，请重新登录。')
        li_idx, div_suffix = self.STADIUMS[name]
        xpath = f'/html/body/div[2]/div[2]/ul/li[{li_idx}]/div[1]/{div_suffix}/span/a'
        el = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        el.click()
        schedule = self._wait_schedule()
        day = self.day_choice()
        if day is None:
            return
        for _ in range(day):
            before = self._schedule()
            self.wait.until(EC.element_to_be_clickable(self.NEXT_DAY)).click()
            schedule = self._wait_schedule(before=before)
        slots = self._scan_choice(schedule[2])
        if slots:
            self.en_list, unconfirmed = self.scan_slots(slots)
            self.scenes(name, ('今天', '明天', '后天')[day], unconfirmed)

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

        return picked[0] if picked else None

    def _scan_choice(self, slots):
        if not slots:
            messagebox.showinfo('场地查询', '学校页面没有提供可扫描的时段。')
            return None
        win = tk.Tk()
        win.title('选择时段（可多选）')
        picked = []
        listbox = tk.Listbox(win, selectmode='extended', width=32, height=min(len(slots), 20))
        for _, label in slots:
            listbox.insert(tk.END, label)

        def confirm(indices):
            if indices:
                picked.extend(slots[index] for index in indices)
                win.destroy()

        listbox.pack(padx=8, pady=6, fill='both', expand=True)
        tk.Button(win, text='扫描选中时段', command=lambda: confirm(listbox.curselection())).pack()
        tk.Button(win, text='扫描全天', command=lambda: confirm(range(len(slots)))).pack(pady=4)
        win.mainloop()
        return picked

    def _schedule(self):
        if not self._is_venue():
            raise ReserveError('场地登录会话已失效，请重新登录。')
        snapshot = self.browser.execute_script('''
            const select = document.querySelector('#starttime');
            if (!select) return null;
            const options = Array.from(select.options)
                .filter(option => !option.disabled && /^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(option.value))
                .map(option => [option.value, option.text.trim() || option.value]);
            const rows = Array.from(document.querySelectorAll('.spacezt'))
                .filter(row => row.getClientRects().length > 0 && row.innerText.trim())
                .map(row => [row, row.id, row.innerText.trim()]);
            return [select, select.value, options, rows];
        ''')
        return snapshot

    def _wait_schedule(self, before=None, selected=None, require_rows=False):
        last, changed_at = None, 0

        def loaded(_):
            nonlocal last, changed_at
            current = self._schedule()
            if not current or (selected is not None and current[1] != selected):
                last = None
                return None
            if (require_rows or (before and before[3])) and not current[3]:
                last = None
                return None
            if before and current[3] == before[3] and (before[3] or current[0] == before[0]):
                last = None
                return None
            now = time.monotonic()
            if current != last:
                last, changed_at = current, now
                return None
            return current if now - changed_at >= 0.4 else None

        return self.wait.until(loaded)

    def _select_slot(self, value):
        before = self._schedule()
        if not before:
            raise ReserveError('未找到学校时段表，请重新进入场馆。')
        if before[1] != value:
            Select(before[0]).select_by_value(value)
            return self._wait_schedule(before=before, selected=value, require_rows=True)[3]
        return self._wait_schedule(selected=value, require_rows=True)[3]

    def scan_slots(self, slots):
        results = []
        for offset, (value, label) in enumerate(slots):
            try:
                rows = self._select_slot(value)
            except (NoSuchElementException, TimeoutException):
                return results, [remaining_label for _, remaining_label in slots[offset:]]
            for index, (_, court_id, status) in enumerate(rows, 1):
                results.append({'time': value, 'label': label, 'idx': index,
                                'court_id': court_id, 'status': status})
        return results, []

    # ===== booking =====
    def scenes(self, name, day, unconfirmed):
        win = tk.Tk()
        win.title(f'{name} · {day}')
        win.geometry('480x460')
        lines = [
            f"{item['label']} · 第 {item['idx']} 场地 · {item['status']}"
            for item in self.en_list
        ]
        if unconfirmed:
            tk.Label(win, text=f'以下时段未能确认或未扫描：{", ".join(unconfirmed)}',
                     wraplength=440, justify='left').pack(fill='x', pady=6)
        scrollbar = tk.Scrollbar(win)
        lb = tk.Listbox(win, width=60, yscrollcommand=scrollbar.set)
        scrollbar.config(command=lb.yview)
        for s in lines:
            lb.insert(tk.END, s)

        def book():
            if unconfirmed:
                return
            try:
                sel = lb.curselection()[0]
            except (IndexError, tk.TclError):
                return
            item = self.en_list[sel]
            if item['status'] != '可预约':
                messagebox.showerror('错误', '请预约可预约的场地')
                return
            if not messagebox.askyesno('提示', '是否确认预定该场地'):
                return
            try:
                rows = self._select_slot(item['time'])
                if item['idx'] > len(rows):
                    raise ReserveError('场地列表已变化，请重新扫描。')
                court, court_id, status = rows[item['idx'] - 1]
                if court_id != item['court_id'] or status != '可预约':
                    raise ReserveError('场地状态已变化，请重新扫描。')
                court.click()
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
                messagebox.showinfo('提示', '已发送预约操作，请在学校页面确认订单状态。')
            except ReserveError as error:
                messagebox.showerror('预约未完成', str(error))
            except WebDriverException:
                messagebox.showerror('预约未确认', '请在学校页面核对订单后，再决定是否重试。')

        tk.Button(win, text='关闭结果，返回场馆选择', command=win.destroy).pack(side='bottom')
        if not unconfirmed:
            tk.Button(win, text='预约选中场地', command=book).pack(side='bottom')
        scrollbar.pack(side='right', fill='y')
        lb.pack(side='left', fill='both', expand=True)
        win.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='华中科技大学体育场地查询')
    parser.add_argument('--env-file', default='.env', help='登录配置文件（默认 .env）')
    parser.add_argument('--headless', action='store_true', help='隐藏 Edge 窗口，仍需桌面显示验证码')
    parser.add_argument('--manual-captcha', action='store_true', help='始终手动输入图形验证码')
    args = parser.parse_args()
    HustReserve(args.env_file, args.headless, args.manual_captcha).run()
