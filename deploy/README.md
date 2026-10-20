# استقرار دموی افرند

این سند سوابق تاریخ‌دار استقرار دمو تا ۱۱ اکتبر ۲۰۲۶ است؛ وضعیت زندهٔ امروز بررسی نشده است. آخرین نسخهٔ ثبت‌شده `20261011-cashback` است و بخش‌های ۶ و ۸ اکتبر فقط سابقه‌اند. ابزارهای مدیریت سرور مانند `enable_https.py` همراه این مخزن منتشر نمی‌شوند؛ دستورهای وابسته به آن‌ها راه‌اندازی مستقل را پوشش نمی‌دهند. اجرای عملیات سرور نیازمند دسترسی و مجوز جداگانه است.

## نسخهٔ کش‌بک — ۱۱ اکتبر ۲۰۲۶

نسخهٔ فعال `afrand-woo-demo:20261011-cashback` از انتشار مستقل `/opt/afrand-woo-demo/releases/20261011-cashback` اجرا می‌شود. کش‌بک پیش‌فرض ۵٪ و برای ثبت تکی یا کل فایل اکسل قابل تنظیم است. فقط کش‌بک محاسبه‌شده در سرور به TeraWallet ارسال می‌شود؛ مبلغ خرید و درصد در شرح تراکنش ثبت می‌شوند. پیش‌نمایش‌های قدیمی برای جلوگیری از واریز کل خرید پذیرفته نمی‌شوند و باید دوباره ساخته شوند.

کانتینر پیشین با نام `afrand-woo-demo-before-cashback` متوقف و نگهداری شده است. دیتابیس، تنظیمات، ورود و پورت برنامه حفظ شدند؛ backup دفتر عملیات در `data/panel.before-20261011-cashback.sqlite3` قرار دارد. هش هر دو vhost و شناسهٔ کانتینرهای دیگر ثابت ماندند؛ health سرویس موجود 200 و چهار سرویس قبلی active بودند. rollback با تعویض نام همین دو کانتینر و بازگرداندن restart policy ممکن است؛ نسخهٔ قبلی کل مبلغ خرید را واریز می‌کند، بنابراین بازگشت فنی رفتار کش‌بک را حذف می‌کند.

۱۰۷ تست واحد و شش گروه پذیرش مرورگر موقت موفق بودند. بررسی عمومی HTTPS و آی‌پی مستقیم، درصد پیش‌فرض/دلخواه، مبلغ کش‌بک در تأیید، نمونهٔ اکسل (۱۵۰٬۰۰۰ تومان خرید و ۷٬۵۰۰ تومان کش‌بک)، ورود/خروج و نمایش موبایل را بدون واریز خارجی تأیید کرد. جزئیات تراکنش آزمایشی واقعی در [مدارک تحویل](../demo/verification.md) ثبت می‌شود.

استقرار اولیه در ۶ اکتبر و نسخهٔ کیف پول در ۸ اکتبر ۲۰۲۶ روی بخش لینوکسی سرور انجام شد. برنامه با نام کانتینر `afrand-woo-demo` در `/opt/afrand-woo-demo` قرار دارد. WooCommerce همچنان همان staging تأییدشده است.

- نشانی مستقیم: `http://141.11.250.83:8087`
- دامنهٔ اختصاصی: `wp-woo-demo.mahdihz.ir`
- نشانی پیشنهادی با HTTPS: `https://wp-woo-demo.mahdihz.ir`؛ اتصال، جست‌وجوی مشتری و موجودی کیف پول، پیش‌نمایش اکسل و نمایش موبایل روی همین نشانی از بیرون سرور تأیید شدند.
- پورت backend فقط روی loopback سرور: `127.0.0.1:5057`
- ورود با صفحهٔ فارسی `/login` داخل برنامه است؛ پنجرهٔ Basic Auth مرورگر از vhost افرند حذف شد. مشخصات ورود مطابق انتخاب کاربر در فایل محلیِ مستثنا از Git، `instance/demo-access.txt` قرار دارد. فقط هش رمز در `app.env` ذخیره می‌شود.
- دیتابیس پایدار: `/opt/afrand-woo-demo/data/panel.sqlite3`؛ نسخهٔ اولیه با SQLite backup از دفتر عملیات محلی منتقل شد.
- تنظیمات و کلیدهای ووکامرس: `/opt/afrand-woo-demo/config/app.env` با مجوز `600`؛ در image یا Git قرار ندارند.

Nginx فقط فایل مستقل `/etc/nginx/sites-available/zzz-afrand-woo-demo` را اضافه می‌کند؛ نام فایل ترتیب سایت پیش‌فرض موجود را حفظ می‌کند. backend با Gunicorn، یک worker و چهار thread اجرا می‌شود. کانتینر سقف ۳۸۴ مگابایت RAM و نیم هسته CPU دارد، با کاربر غیر root، filesystem فقط خواندنی، حجم لاگ محدود و restart خودکار اجرا می‌شود. فقط پوشهٔ دیتابیس و `/tmp` قابل نوشتن‌اند.

## DNS و HTTPS

هنگام شروع استقرار این نام `NXDOMAIN` بود. در ادامهٔ همان جلسه، دامنه از طریق Cloudflare به همین vhost رسید و HTTPS روی origin و دامنهٔ عمومی تأیید شد. گواهی Let's Encrypt برای همین نام تا ۴ ژانویهٔ ۲۰۲۷ اعتبار اولیه دارد و تمدید خودکار فعال است. برای ایجاد مجدد رکورد، zone برابر `mahdihz.ir`، نام رکورد `wp-woo-demo` و IP سرور `141.11.250.83` است.

تایمر مستقل `afrand-demo-https.timer` هر ۱۵ دقیقه مسیر عمومی challenge را بررسی می‌کند. پس از رسیدن درخواست به همین برنامه، اسکریپت `enable_https.py` با Certbot webroot گواهی می‌گیرد، فقط vhost افرند را به HTTPS تغییر می‌دهد و HTTP دامنه را به HTTPS هدایت می‌کند. سایت‌های دیگر متوقف نمی‌شوند. hook تمدید گواهی فقط برای همین دامنه تعریف شده است. اجرای فوری پس از ثبت رکورد:

```sh
systemctl start afrand-demo-https.service
journalctl -u afrand-demo-https.service --no-pager -n 20
```

نشانی مستقیم HTTP است؛ برای استفاده و اشتراک‌گذاری دمو، نشانی HTTPS دامنه ترجیح دارد. پراکسی Cloudflare در بررسی نهایی فعال بود و با این استقرار کار کرد؛ نیازی به تغییر تنظیمات سراسری zone نشد.

## نگهداری و بازگشت تاریخی نسخهٔ ۸ اکتبر

دستورهای بازگشت این بخش مربوط به نسخهٔ کیف پول پیش از کش‌بک هستند و مسیر بازگشت نسخهٔ کش‌بک محسوب نمی‌شوند. عبارت‌های «فعلی» و «فعال» در ادامه به تاریخ همان بخش اشاره دارند.

```sh
docker logs --tail 100 afrand-woo-demo
docker inspect --format '{{.State.Status}} {{.Config.Image}}' afrand-woo-demo
docker restart afrand-woo-demo
nginx -t
```

برای تعویض نسخه، فقط image و کانتینر همین برنامه را تغییر بده و mount دیتابیس را حفظ کن. نسخهٔ فعلی `afrand-woo-demo:20261008-wallet` است؛ نسخهٔ پیش از تبدیل به کیف پول با نام `afrand-woo-demo-before-wallet` متوقف و برای بازگشت نگهداری شده است. این نسخهٔ بازگشت رابط قدیمی سفارش را دارد و فقط برای بازگشت فنی است؛ ورود داخلی دارد و بازگشت به آن تغییری در Nginx لازم ندارد:

```sh
docker stop afrand-woo-demo
docker rename afrand-woo-demo afrand-woo-demo-failed
docker rename afrand-woo-demo-before-wallet afrand-woo-demo
docker update --restart=unless-stopped afrand-woo-demo
cp /opt/afrand-woo-demo/config/app.env.before-wallet /opt/afrand-woo-demo/config/app.env
chmod 600 /opt/afrand-woo-demo/config/app.env
docker start afrand-woo-demo
```

حذف یا جایگزینی دیتابیس برای بازگشت نسخه لازم نیست. هیچ `docker prune`، ارتقای سیستم‌عامل یا restart برنامه‌های دیگر انجام نشده است.

نسخهٔ قدیمی‌تر `afrand-woo-demo-before-login` فاقد ورود داخلی است؛ انتشار آن فقط با بازگرداندن محافظت قبلی vhost از فایل `zzz-afrand-woo-demo.before-session-login` مجاز است.

## بررسی تحویل

```powershell
$env:AFRAND_DEMO_URL='http://141.11.250.83:8087'
uv run --with playwright python scripts/verify_deployment.py
$env:AFRAND_DEMO_URL='https://wp-woo-demo.mahdihz.ir'
uv run --with playwright python scripts/verify_deployment.py
```

این بررسی از بیرون سرور، ورود، اتصال، جست‌وجوی مشتری و موجودی کیف پول، دانلود نمونه، پیش‌نمایش واقعی Excel، رد درخواست cross-origin و نمایش موبایل را بررسی می‌کند؛ مشتری جدید نمی‌سازد و واریز انجام نمی‌دهد؛ تأییدها فقط باز و لغو می‌شوند. گزارش محلی در `instance/verification/wallet-deployment/` ذخیره می‌شود.

ورود داخلی در ۶ اکتبر ۲۰۲۶ روی دامنه بررسی شد: صفحهٔ سادهٔ ورود، پیام رمز اشتباه، ورود درست، ماندن ورود پس از reload، خروج، محافظت API بدون `WWW-Authenticate`، اتصال ووکامرس و پیش‌نمایش اکسل. ۸۰ تست واحد موفق بودند. session هشت ساعت اعتبار دارد؛ cookie در HTTPS به‌صورت Secure و همیشه HttpOnly/SameSite=Lax است. ورود و خروج CSRF token دارند و پاسخ‌ها cache نمی‌شوند.

پیش از این تغییر، هر دو نشانی بررسی استقرار را پاس کرده بودند. اصلاح املا روی رابط، راهنماها و نمونه‌های اکسل اعمال شد؛ فقط فیلدهای نام/توضیحات دو کالای ساختگیِ علامت‌گذاری‌شده و نام خانوادگی سه مشتری ساختگی قبلی در staging اصلاح شدند. خواندن مجدد تأیید کرد قیمت، SKU، ایمیل و آدرس‌ها تغییر نکرده‌اند؛ سفارش یا مشتری جدیدی ساخته نشد.

مبنای تنظیم Gunicorn: [مستندات رسمی تنظیمات](https://docs.gunicorn.org/en/stable/settings.html). مبنای صدور گواهی بدون توقف سرویس‌های موجود: [مستندات رسمی Certbot webroot](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot).


## نسخهٔ کیف پول — ۸ اکتبر ۲۰۲۶

نسخهٔ فعال `afrand-woo-demo:20261008-wallet` با `WALLET_MODE=true` مشتری بدون ایمیل می‌سازد و فقط کیف پول TeraWallet را شارژ می‌کند. مسیرهای محصول/سفارش در حالت فعال 404 هستند. پوشهٔ انتشار `/opt/afrand-woo-demo/releases/20261008-wallet` و کانتینر قبلیِ متوقف `afrand-woo-demo-before-wallet` است؛ دیتابیس پایدار جایگزین نشده است.

افزونهٔ محدود WordPress `afrand-wallet-bridge` نسخهٔ 1.0.2 با کلیدهای قبلی WooCommerce نصب و فعال شد؛ رمز ادمین در سرور برنامه ذخیره نشده است. واریز از متد اصلی TeraWallet انجام می‌شود و شناسه/نتیجه در جدول اختصاصی WordPress و تراکنش اصلی نگهداری می‌شود. [راهنمای اتصال](../wordpress/README.md).

۱۰۱ تست واحد و بررسی مرورگر با backend موقت موفق بودند. پذیرش واقعی روی دامنه، اجرای `f1fb1696`، ساخت مشتری‌های علامت‌گذاری‌شدهٔ 10/11 و تراکنش‌های 2/3/4/5 با مجموع ۲۵۰۰ تومان را تأیید کرد؛ ساخت حساب بدون ایمیل، واریز مشتری موجود، بازیابی قطع پاسخ، اکسل، تکرار بدون واریز دوباره و خواندن مستقل تراکنش‌های TeraWallet موفق بودند. بررسی اولیه نیز مشتری 9 و تراکنش 1 به مبلغ ۱۲۵۰ تومان ایجاد کرده بود. هیچ سفارش تازه‌ای در این پذیرش ایجاد نشد. داده‌های آزمایشی و تراکنش‌ها برای امکان بررسی حفظ شده‌اند.

شناسهٔ کانتینرهای دیگر و هش تنظیمات هر دو vhost تغییری نکردند؛ سرویس‌های موجود فعال و health سایت دیگر 200 بود. نسخهٔ کیف پول هیچ تغییر Nginx، DNS، فایروال یا سرویس‌های دیگر لازم نداشت. کلیدهای اتصال، هش ورود و mount دیتابیس حفظ شدند. گزارش واقعی در `instance/verification/wallet-live/f1fb1696/report.json` است. این مسیرها و گزارش‌های دارای دادهٔ آزمایشی محلی از Git مستثنا هستند.

بررسی نهایی نسخه روی دامنه و آی‌پی مستقیم موفق بود: ورود اشتباه/درست، موجودی، لغو پاپ‌آپ واریز، دانلود و پیش‌نمایش نمونه با دو ردیف معتبر/یک خطا، لغو واریز اکسل، خروج و موبایل بدون خطای JavaScript؛ صفر واریز یا مشتری جدید در این بررسی. برای سازگاری آی‌پی HTTP، شناسهٔ تصادفی درخواست از `crypto.getRandomValues` ساخته می‌شود. عنوان صفحهٔ ورود هم کیف پول است. کانتینر پیش از این اصلاح ظاهری/مرورگر، متوقف با نام `afrand-woo-demo-wallet-before-polish` نگهداری شده؛ نسخهٔ فعال همان tag کیف پول است.


## Manual purchase wording revision — 2026-10-08

At the user’s request, the manual payment field, description, button, confirmation and success text now identify the amount as «مبلغ خرید کاربر». This is a presentation change: the existing native wallet credit, payload, idempotency and Excel behavior remain unchanged; no WooCommerce purchase/order is created. Deployed image `afrand-woo-demo:20261008-purchase`; preceding wallet container retained stopped as `afrand-woo-demo-wallet-before-purchase`. Persistent data/configuration preserved. All 101 tests passed. Read-only browser acceptance passed both HTTPS domain and direct IP, including the new field and confirmation wording, customer search/balance, login, Excel preview and mobile checks; zero external credits, JavaScript errors or native alerts. Other running container identities and Nginx vhost hashes were unchanged; existing health returned 200 and services remained active.
