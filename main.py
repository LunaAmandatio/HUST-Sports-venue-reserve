import argparse
import tkinter as tk

from src.legacy_venue_search import VenueSearch
from src.repeat_reservation import RepeatReservation
from src.scheduled_reservation import ScheduledReservation


MODES = {'search': VenueSearch, 'scheduled': ScheduledReservation, 'repeat': RepeatReservation}


# 程序入口：未指定模式时先选择，确认后才启动登录浏览器。
def choose_mode():
    window = tk.Tk()
    window.title('HUST · 选择模式')
    selected = None

    def choose(mode):
        nonlocal selected
        selected = mode
        window.destroy()

    tk.Label(window, text='请选择使用模式').pack(padx=32, pady=12)
    for mode, application in MODES.items():
        tk.Button(window, text=application.MODE_NAME, width=28,
                  command=lambda key=mode: choose(key)).pack(padx=16, pady=4)
    tk.Button(window, text='退出', command=window.destroy).pack(pady=12)
    window.mainloop()
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description='华中科技大学体育场地查询和预约')
    parser.add_argument('--mode', choices=MODES, help='直接进入指定模式，跳过模式选择窗口')
    parser.add_argument('--env-file', default='.env', help='登录配置文件（默认 .env）')
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument('--headed', dest='headless', action='store_false', help='调试时显示 Edge 窗口')
    browser.add_argument('--headless', dest='headless', action='store_true', help='隐藏 Edge 窗口（默认）')
    parser.set_defaults(headless=True)
    parser.add_argument('--manual-captcha', action='store_true', help='始终手动输入图形验证码')
    parser.add_argument('--manual-start', action='store_true', help='等待点击「登录并查询」后再启动浏览器')
    args = parser.parse_args(argv)

    mode = args.mode or choose_mode()
    if mode:
        MODES[mode](args.env_file, args.headless, args.manual_captcha).run(manual_start=args.manual_start)


if __name__ == '__main__':
    main()
