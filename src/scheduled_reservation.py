from datetime import datetime, time, timedelta
from tkinter import messagebox

from hust_reserve import HustReserve


class ScheduledReservation(HustReserve):
    MODE_NAME = '北京时间 8 点预约'
    MULTIPLE_SLOTS = False
    START_GRACE = timedelta(minutes=1)

    # 固定北京时间，避免在其他时区启动时排错预约时间。
    def next_run(self):
        now = self.school_now()
        target = datetime.combine(now.date(), time(8), tzinfo=self.SCHOOL_TZ)
        return target if target >= now else target + timedelta(days=1)

    # 到点只执行一轮，目标仍是用户选择的绝对日期。
    def run_selection(self, name, visit_date, slots):
        target = self.next_run()
        if visit_date < target.date():
            messagebox.showwarning(self.MODE_NAME, '目标日期早于下次 8 点执行时间，请另选日期。')
            return
        if not messagebox.askyesno(
            self.MODE_NAME,
            f'{name}\n{visit_date:%Y-%m-%d} · {slots[0][1]}\n\n'
            f'北京时间 {target:%Y-%m-%d %H:%M} 检查一次，发现空场后自动提交一次预约。\n'
            '等待时请保持程序和浏览器开启。是否启用？',
        ):
            return
        if not self.wait_until(self.MODE_NAME, target):
            return
        if self.school_now() > target + self.START_GRACE:
            messagebox.showwarning(self.MODE_NAME, '已错过执行时间，本次未提交预约。')
            return
        if not self.attempt_reservation(name, visit_date, slots):
            messagebox.showinfo(self.MODE_NAME, '所选时段没有可预约场地。')
