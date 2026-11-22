# HUST 体育场地查询

使用华中科技大学统一身份认证登录体育场地系统。通过 `main.py` 选择即时查询、定时预约或重复查询；三个模式共用同一套登录、双因子认证和场地状态读取逻辑。

## 安装与运行

需要 Python 3.10+、Tk 桌面环境和 Microsoft Edge。Windows 和 macOS 可使用 python.org 提供的 Python；Linux 如果缺少 Tk，请安装发行版的 Tkinter 软件包，例如 Ubuntu 的 `python3-tk`。Selenium 会尝试管理对应的 Edge WebDriver，首次运行可能需要联网；离线环境可将与 Edge 匹配的 `msedgedriver` 放在 `PATH` 中。

```sh
python -m pip install -r requirements.txt
python main.py
```

运行前，将 `.env.example` 复制到同目录的 `.env`（Windows PowerShell：`Copy-Item .env.example .env`；macOS/Linux：`cp .env.example .env`），填入自己的 `account` 和 `password`。`.env` 不纳入版本控制，本地文件仍是明文，请仅在自己的设备上保存。也可通过环境变量提供账号密码，或用 `--env-file` 指定其他配置文件。

1. 选择模式后，已配置账号密码时立即登录。可用 `--mode search`、`--mode scheduled` 或 `--mode repeat` 跳过模式选择窗口；用 `--manual-start` 可改为点击 **登录并查询** 后启动。学校显示图形验证码时，程序使用现有的 GIF 识别脚本，识别失败或被拒后改为手动输入；用 `--manual-captcha` 可关闭自动识别及其 CDN 依赖。
2. Edge 默认无头运行，但模式选择、验证码和付款码仍使用本地桌面窗口。仅在学校要求双因子认证时才提示输入动态码。调试时可用 `--headed` 显示 Edge。
3. 选择场馆、日期和时段。时段来自学校页面；若无法确认场地状态，程序不会将其当作无空场，也不会提交预约。

如果学校将浏览器跳转到场地系统的 HTTP 地址，程序会尝试重新打开固定的 HTTPS 场地入口。只有在进入 HTTPS 场馆列表后才认为登录完成；不会在 HTTP 页面输入账号、密码或动态码。

## 三种模式

- `src/legacy_venue_search.py`：即时查询，可多选时段或扫描全天。扫描不提交预约；需要预约时，在完整的结果中选中空场并手动确认。
- `src/scheduled_reservation.py`：选择一个时段，确认后在下一个北京时间 08:00 检查一次。有空场时最多提交一次预约；程序或浏览器关闭、登录会话过期或错过执行时间时不会执行预约。
- `src/repeat_reservation.py`：选择一个时段，确认后最多检查 12 次，每轮间隔至少 5 秒。发现可预约场地后最多提交一次；状态不明时停止，不自动补交。

预约提交后，如果学校支付页面展示了可识别的付款二维码，程序会在本地弹窗中显示。请核对收款方、订单和金额后自行扫码；付款码不会写入项目文件。如果未识别到唯一付款码，请在学校系统的预约记录中查看订单，勿重复提交。

GitHub Actions 在 Windows、macOS 和 Linux 上检查 Python 语法、模块导入和命令行入口，不访问学校。仓库仅分发源码和配置，不再分发 EXE 或 ZIP。

三个模式都继承 `hust_reserve.py` 的 `HustReserve`。浏览器脚本单独位于 `src/browser/captcha.js` 和 `src/browser/payment_code.js`，由 Selenium 注入，不需要安装 Node.js。
