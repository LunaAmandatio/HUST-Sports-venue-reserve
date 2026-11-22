# HUST 体育场地查询

使用华中科技大学统一身份认证登录体育场地系统。登录后选择一个场馆和日期，可扫描选中时段或全天；扫描只读取场地状态，不会提交预约。

## 安装与运行

需要 Python 3.10+、Tk 桌面环境和 Microsoft Edge。Windows 和 macOS 可使用 python.org 提供的 Python；Linux 如果缺少 Tk，请安装发行版的 Tkinter 软件包，例如 Ubuntu 的 `python3-tk`。Selenium 会尝试管理对应的 Edge WebDriver，首次运行可能需要联网；离线环境可将与 Edge 匹配的 `msedgedriver` 放在 `PATH` 中。

```sh
python -m pip install -r requirements.txt
python hust_reserve.py
```

运行前，将 `.env.example` 复制到同目录的 `.env`（Windows PowerShell：`Copy-Item .env.example .env`；macOS/Linux：`cp .env.example .env`），填入自己的 `account` 和 `password`。`.env` 不纳入版本控制，本地文件仍是明文，请仅在自己的设备上保存。也可通过环境变量提供账号密码，或用 `--env-file` 指定其他配置文件。

1. 点击 **登录并查询**。程序自动填写账号密码；学校显示图形验证码时，使用现有的 GIF 识别脚本，识别失败或被拒后改为手动输入。用 `--manual-captcha` 可完全关闭图形码自动识别及其 CDN 依赖。
2. 仅在学校要求双因子认证时，输入学校发来的动态码。动态码、验证码图片和浏览器会话不保存到项目中。用 `--headless` 可隐藏 Edge，但验证码窗口仍需要桌面环境。
3. 选择场馆以及今天、明天或后天，点 **扫描选中时段** 或 **扫描全天**。时段由学校页面提供；若状态更新无法确认，程序会标明受影响的时段，不把它们当作无空场。
4. 需要预约时，在完整的扫描结果中选中状态为“可预约”的场地，再手动确认。最终以学校页面显示的订单状态为准。

如果学校将浏览器跳转到场地系统的 HTTP 地址，程序会尝试重新打开固定的 HTTPS 场地入口。只有在进入 HTTPS 场馆列表后才认为登录完成；不会在 HTTP 页面输入账号、密码或动态码。

## 验证与旧版

- GitHub Actions 在 Windows、macOS 和 Linux 上验证 Python 语法、依赖导入和新版命令行入口；不访问学校，也不验证真实登录或预约。
- `legacy_venue_search.py`、`scheduled_reservation.py`、`repeat_reservation.py` 是旧版脚本，不包含新版双因子流程。旧版定时脚本另需安装 `APScheduler` 3.x。
- 仓库仅分发源码和配置，不再分发旧版 EXE 或 ZIP。
