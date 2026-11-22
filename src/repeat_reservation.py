from datetime import timedelta
from tkinter import messagebox

from hust_reserve import HustReserve


class RepeatReservation(HustReserve):
    MODE_NAME = '重复查询并预约'
    MULTIPLE_SLOTS = False
    INTERVAL_SECONDS = 5
    MAX_CHECKS = 12

    # 启用前确认目标；每轮结束后再计时，不并发访问学校页面。
    def run_selection(self, name, visit_date, slots):
        if not messagebox.askyesno(
            self.MODE_NAME,
            f'{name}\n{visit_date:%Y-%m-%d} · {slots[0][1]}\n\n'
            f'最多检查 {self.MAX_CHECKS} 次，每轮相隔至少 {self.INTERVAL_SECONDS} 秒。'
            '发现空场后自动提交一次预约。是否启用？',
        ):
            return

        for attempt in range(1, self.MAX_CHECKS + 1):
            if self.attempt_reservation(name, visit_date, slots):
                return
            if attempt < self.MAX_CHECKS:
                next_check = self.school_now() + timedelta(seconds=self.INTERVAL_SECONDS)
                if not self.wait_until(f'{self.MODE_NAME}（已检查 {attempt}/{self.MAX_CHECKS} 次）', next_check):
                    return
        messagebox.showinfo(self.MODE_NAME, '达到检查次数上限，没有发现可预约场地。')
