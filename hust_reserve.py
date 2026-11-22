import os
import re
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, simpledialog
from urllib.parse import urlsplit

from dotenv import dotenv_values
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException, NoSuchWindowException, StaleElementReferenceException, TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


BROWSER_SCRIPTS = Path(__file__).with_name('src') / 'browser'
CAPTCHA_JS = (BROWSER_SCRIPTS / 'captcha.js').read_text(encoding='utf-8')
PAYMENT_CODE_JS = (BROWSER_SCRIPTS / 'payment_code.js').read_text(encoding='utf-8')


class ReserveError(Exception):
    pass


class HustReserve:
    MODE_NAME = '场地查询'
    MULTIPLE_SLOTS = True
    SCHOOL_TZ = timezone(timedelta(hours=8))
    CAS_HOST = 'pass.hust.edu.cn'
    VENUE_HOST = 'pecg.hust.edu.cn'
    PAYMENT_HOST = 'pay.hust.edu.cn'
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

    def __init__(self, env_file='.env', headless=True, manual_captcha=False):
        self.env_file = env_file
        self.headless = headless
        self.browser = None
        self.wait = None
        self.force_manual_captcha = manual_captcha

    # 程序入口：配置齐全时直接登录；手动启动模式保留确认窗口。
    def run(self, manual_start=False):
        if not manual_start:
            account, password = self._credentials()
            if account and password:
                self.start(account, password)
                return

        root = tk.Tk()
        root.title(f'HUST · {self.MODE_NAME}')
        root.geometry('500x500')
        tk.Label(
            root,
            text='登录后可按场馆、日期和时段查询场地状态',
            bg='green', fg='white', width=100, height=10,
        ).pack()

        def on_start():
            account, password = self._credentials()
            if not account or not password:
                messagebox.showerror('缺少登录配置', '请在 .env 或 --env-file 指定的文件中填写 account 和 password。',
                                     parent=root)
                return
            root.destroy()
            self.start(account, password)

        tk.Button(root, text='登录并查询', width=20, height=5,
                  command=on_start, bg='red').place(x=170, y=250)
        tk.Label(root, text='本程序仅供交流学习使用，切勿用于非法用途',
                 width=100, height=10).pack(side='bottom')
        root.mainloop()

    # 统一身份认证：先识别页面状态，学校要求时才输入验证码。
    def _credentials(self):
        config = dotenv_values(self.env_file, interpolate=False)
        account = (os.environ.get('account', config.get('account')) or '').strip()
        password = os.environ.get('password', config.get('password')) or ''
        return account, password

    def start(self, account, password):
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

    # 场馆选择与学校页面定位。
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
        day = self.day_choice()
        if day is None:
            return
        visit_date = self.school_now().date() + timedelta(days=day)
        schedule = self._open_stadium(name, day)
        slots = self._scan_choice(schedule[2])
        if slots:
            self.run_selection(name, visit_date, slots)

    def run_selection(self, name, visit_date, slots):
        raise NotImplementedError('由所选模式处理查询或预约')

    def _open_stadium(self, name, days_ahead):
        if not self._is_venue():
            raise ReserveError('场地登录会话已失效，请重新登录。')
        li_idx, div_suffix = self.STADIUMS[name]
        xpath = f'/html/body/div[2]/div[2]/ul/li[{li_idx}]/div[1]/{div_suffix}/span/a'
        el = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        el.click()
        schedule = self._wait_schedule()
        for _ in range(days_ahead):
            before = schedule
            self.wait.until(EC.element_to_be_clickable(self.NEXT_DAY)).click()
            schedule = self._wait_schedule(before=before)
        return schedule

    # 日期和时段选择：时段以学校页面为准，不在各模式中重复写死。
    def school_now(self):
        return datetime.now(self.SCHOOL_TZ)

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
        win.title('选择时段（可多选）' if self.MULTIPLE_SLOTS else '选择一个预约时段')
        picked = []
        listbox = tk.Listbox(win, selectmode='extended' if self.MULTIPLE_SLOTS else 'browse',
                             width=32, height=min(len(slots), 20))
        for _, label in slots:
            listbox.insert(tk.END, label)

        def confirm(indices):
            if indices:
                picked.extend(slots[index] for index in indices)
                win.destroy()

        listbox.pack(padx=8, pady=6, fill='both', expand=True)
        tk.Button(win, text='扫描选中时段' if self.MULTIPLE_SLOTS else '确定时段',
                  command=lambda: confirm(listbox.curselection())).pack()
        if self.MULTIPLE_SLOTS:
            tk.Button(win, text='扫描全天', command=lambda: confirm(range(len(slots)))).pack(pady=4)
        win.mainloop()
        return picked

    # 扫描场地：切换时段后等待新状态，避免把上一时段的空场算进来。
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

    # 定时和轮询都重新加载目标日期；跨午夜不把「今天」误当成新日期。
    def scan_target(self, name, visit_date, slots):
        days_ahead = (visit_date - self.school_now().date()).days
        if not 0 <= days_ahead <= 2:
            raise ReserveError('目标日期不在学校页面可查询的三天内。')
        self._open_venue_index()
        self._open_stadium(name, days_ahead)
        return self.scan_slots(slots)

    def wait_until(self, title, target):
        win = tk.Tk()
        win.title(title)
        countdown = tk.StringVar(master=win)
        tk.Label(win, textvariable=countdown).pack(padx=24, pady=12)
        tk.Button(win, text='取消', command=win.destroy).pack(pady=8)
        finished = False

        def tick():
            nonlocal finished
            remaining = (target - self.school_now()).total_seconds()
            if remaining <= 0:
                finished = True
                win.destroy()
                return
            countdown.set(f'北京时间 {target:%m-%d %H:%M:%S} 执行，剩余 {int(remaining) + 1} 秒')
            win.after(min(1000, max(1, int(remaining * 1000))), tick)

        win.after(0, tick)
        win.mainloop()
        return finished

    # 预约前重新检查该时段和场地状态；提交后不把点击成功当成订单成功。
    def _book_court(self, item):
        rows = self._select_slot(item['time'])
        if item['idx'] > len(rows):
            raise ReserveError('场地列表已变化，请重新扫描。')
        court, court_id, status = rows[item['idx'] - 1]
        if court_id != item['court_id'] or status != '可预约':
            raise ReserveError('场地状态已变化，请重新扫描。')
        court.click()
        try:
            identity = self.browser.find_element(By.ID, 'partnerCardType1')
            self.browser.execute_script('arguments[0].click();', identity)
        except NoSuchElementException:
            pass
        confirmation = (By.XPATH, '//*[@id="command"]/div[3]/input[3]')
        self.wait.until(EC.element_to_be_clickable(confirmation)).click()
        try:
            decision = self.browser.find_element(By.XPATH, '//*[@id="dialog_1"]/div[2]/div/div/div[2]/div')
            self.browser.execute_script('arguments[0].click();', decision)
        except NoSuchElementException:
            pass

    def _payment_codes(self):
        url = urlsplit(self.browser.current_url)
        if url.scheme != 'https' or url.netloc not in (self.VENUE_HOST, self.PAYMENT_HOST):
            return []
        return self.browser.execute_script(PAYMENT_CODE_JS)

    def _wait_payment_code(self, booking_window, old_windows, previous_codes):
        def find_code(_):
            matches = []
            for window in self.browser.window_handles:
                if window != booking_window and window in old_windows:
                    continue
                try:
                    self.browser.switch_to.window(window)
                    for element in self._payment_codes():
                        if window != booking_window or element.id not in previous_codes:
                            matches.append((window, element))
                except NoSuchWindowException:
                    continue
            if len(matches) != 1:
                return None
            window, element = matches[0]
            try:
                self.browser.switch_to.window(window)
                return element.screenshot_as_base64
            except NoSuchWindowException:
                return None

        try:
            return self.wait.until(find_code)
        finally:
            if booking_window in self.browser.window_handles:
                try:
                    self.browser.switch_to.window(booking_window)
                except NoSuchWindowException:
                    pass

    # 付款码仅在本地窗口显示，用户核对订单和金额后自行扫码。
    def show_payment_code(self, image_data, parent):
        win = tk.Toplevel(parent) if parent else tk.Tk()
        win.title('订单待付款 · 支付二维码')
        image = tk.PhotoImage(master=win, data=image_data, format='png')
        tk.Label(win, text='请先核对收款方、订单和金额，再用手机扫码。').pack(padx=16, pady=8)
        tk.Label(win, image=image).pack(padx=12, pady=8)
        tk.Button(win, text='关闭', command=win.destroy).pack(pady=8)
        if parent:
            win.transient(parent)
            win.grab_set()
            win.wait_window()
        else:
            win.mainloop()

    def show_booking_result(self, item, parent=None):
        try:
            booking_window = self.browser.current_window_handle
            old_windows = set(self.browser.window_handles)
            previous_codes = {element.id for element in self._payment_codes()}
            self._book_court(item)
        except ReserveError as error:
            messagebox.showerror('预约未完成', str(error))
            return
        except WebDriverException:
            messagebox.showerror('预约未确认', '请在学校系统核对订单后，再决定是否重试。')
            return

        try:
            payment_code = self._wait_payment_code(booking_window, old_windows, previous_codes)
            self.show_payment_code(payment_code, parent)
        except (WebDriverException, tk.TclError):
            messagebox.showinfo('请核对订单', '已提交预约操作，但未取得唯一的付款码。'
                                '请在学校场地系统的预约记录中查看订单和付款状态，勿重复提交。')

    def attempt_reservation(self, name, visit_date, slots):
        """返回 True 表示任务应停止；只有确认无空场时才允许下一轮。"""
        results, unconfirmed = self.scan_target(name, visit_date, slots)
        if unconfirmed:
            messagebox.showwarning('状态未确认', '未能确认学校页面的最新场地状态，已停止。')
            return True
        available = next((item for item in results if item['status'] == '可预约'), None)
        if available is None:
            return False
        self.show_booking_result(available)
        return True

    # 即时检索的结果窗口：由用户选择场地并单独确认预约。
    def scenes(self, name, visit_date, results, unconfirmed):
        win = tk.Tk()
        win.title(f'{name} · {visit_date:%Y-%m-%d}')
        win.geometry('480x460')
        lines = [
            f"{item['label']} · 第 {item['idx']} 场地 · {item['status']}"
            for item in results
        ]
        if unconfirmed:
            tk.Label(win, text=f'以下时段未能确认或未扫描：{", ".join(unconfirmed)}',
                     wraplength=440, justify='left').pack(fill='x', pady=6)
        scrollbar = tk.Scrollbar(win)
        lb = tk.Listbox(win, width=60, yscrollcommand=scrollbar.set)
        scrollbar.config(command=lb.yview)
        for line in lines:
            lb.insert(tk.END, line)

        def book():
            if unconfirmed:
                return
            try:
                sel = lb.curselection()[0]
            except (IndexError, tk.TclError):
                return
            item = results[sel]
            if item['status'] != '可预约':
                messagebox.showerror('错误', '请预约可预约的场地')
                return
            if not messagebox.askyesno('提示', '是否确认预定该场地'):
                return
            self.show_booking_result(item, parent=win)

        tk.Button(win, text='关闭结果，返回场馆选择', command=win.destroy).pack(side='bottom')
        if not unconfirmed:
            tk.Button(win, text='预约选中场地', command=book).pack(side='bottom')
        scrollbar.pack(side='right', fill='y')
        lb.pack(side='left', fill='both', expand=True)
        win.mainloop()
