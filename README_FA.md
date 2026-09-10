# VPNGate Clean Mihomo · اشتراک بهینه‌ساز زندهٔ VPNGate با سنجش پاکی آی‌پی

<p align="center">
  <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a> · <b>فارسی</b>
</p>

<div dir="rtl" lang="fa">

یک <b>ورکر تک‌فایلی Cloudflare</b> که فهرست عمومی نودهای OpenVPNِ
<a href="https://www.vpngate.net">VPN Gate</a> (پروژهٔ آزمایشی دانشگاه تسوکوبا) را به‌صورت زنده دریافت می‌کند،
<b>پاکی / ریسک کلاهبرداریِ هر آی‌پی خروجی را به‌صورت خودکار می‌سنجد</b>، امکان مرور و فیلتر کردن در یک صفحهٔ وب را
فراهم می‌کند و یک کانفیگ آمادهٔ اشتراک برای
<a href="https://wiki.metacubex.one/config/proxies/openvpn/">Mihomo (هستهٔ Clash.Meta)</a> خروجی می‌دهد — از جمله
حالت «اشتراک پاک» که فقط آی‌پی‌های مسکونیِ سالم را نگه می‌دارد و پروکسی‌ها/دیتاسنترهای شناخته‌شده را حذف می‌کند.

همچنین یک <b>لایهٔ تبدیل اختیاری روی VPS</b> (پوشهٔ <code dir="ltr">vps-relay/</code>) ارائه شده است: روی سرور
خودتان با OpenVPN و مسیریابی مبتنی بر سیاست (policy routing)، ترافیک را از یک آی‌پی مسکونیِ کم‌ریسکِ انتخاب‌شده
به‌صورت خودکار خارج می‌کند و با یک نگهبان (watchdog) گذار خودکار (failover) دارد، در حالی که نقطهٔ ورودیِ سمت
کاربر هیچ‌گاه تغییر نمی‌کند.

<blockquote>
این پروژه از
<a href="https://github.com/RememberOurPromise/OpenVPNGate4Mihomo">RememberOurPromise/OpenVPNGate4Mihomo</a>
(مجوز Unlicense / مالکیت عمومی) مشتق شده است. بر پایهٔ «مرور + تبدیل تک‌نودِ» آن، قابلیت‌های
<b>پروفایل ریسک آی‌پی، اشتراک پاک، کانفیگ کامل قابل‌اشتراک و انتخابگر خودکار روی VPS</b> اضافه شده است؛
جزئیات در بخش <a href="#-سپاسگزاری">سپاسگزاری</a>.
</blockquote>

---

## 🖼️ پیش‌نمایش

**① مرورگر نودها: مرتب‌سازی بر اساس «پاکی» — آی‌پی‌های مسکونی/اپراتوریِ سالم (سبز ۱۰۰) بالا می‌آیند و پروکسی/دیتاسنترهای شناخته‌شده قرمز می‌شوند:**

![مرورگر نودها مرتب‌شده بر اساس پاکی](assets/01-clean-dashboard.png)

**② تبدیل هر نود به قطعهٔ کانفیگ Mihomo (Clash.Meta) با یک کلیک — کپی یا دانلود:**

![تبدیل یک‌کلیکی OpenVPN به Mihomo](assets/02-mihomo-convert.png)

**③ آدرس <code dir="ltr">/sub?clean=1</code> مستقیماً یک کانفیگ کاملِ قابل‌اشتراک خروجی می‌دهد — کنار هر نود امتیاز پاکی درج شده و یک گروه خودکارِ url-test وجود دارد (بلوک‌های گواهی در تصویر فشرده شده‌اند):**

![کانفیگ کامل Mihomo خروجی‌گرفته‌شده از اشتراک پاک](assets/03-clean-subscription.png)

---

## ✨ ویژگی‌ها

### موارد افزوده‌شده نسبت به پروژهٔ اصلی
- 🧼 <b>موتور سنجش پاکی / ریسک آی‌پی</b>: برای هر آی‌پی چند سیگنال —
  «نشان پروکسی، نشان دیتاسنتر/میزبانی، نشان شبکهٔ موبایل، کلیدواژه‌های ASN و ISP، نوع کسب‌وکار» — ترکیب شده و
  یک امتیاز ریسک بین <code dir="ltr">0–100</code> و امتیاز پاکی
  <code dir="ltr">clean = 100 - risk</code> در سه دستهٔ <b>پاک / متوسط / پرریسک</b> تولید می‌شود.
- 🎯 <b>حالت اشتراک پاک</b>: با <code dir="ltr">clean=1</code> فقط آی‌پی‌هایی نگه داشته می‌شوند که پروکسیِ
  شناخته‌شده و دیتاسنتر نباشند و <code dir="ltr">clean≥50</code> داشته باشند (آی‌پی مسکونی/اپراتوری)؛ با
  <code dir="ltr">maxrisk</code> می‌توان سقف ریسک تعیین کرد.
- 🔁 <b>کانفیگ کامل و مستقیماً قابل‌اشتراک</b>: آدرس <code dir="ltr">/sub</code> شامل
  <code dir="ltr">proxies + proxy-groups (آزمون سرعت خودکار url-test) + rules</code> است؛ کافی است یک آدرس را در
  کلاینت وارد کنید و نیازی به سرهم‌بندی دستی نیست. پروژهٔ اصلی فقط تک‌نود را در مرورگر تبدیل می‌کرد.
- 🛡️ <b>مقاوم‌سازی خروجی</b>: تمام متن‌های آزادِ طرف‌ثالث (نام ISP، نام میزبان، نام گروه دلخواه و...) از
  کاراکترهای کنترلی پاک‌سازی می‌شوند تا تزریق خط‌شکنندهٔ YAML ممکن نباشد.
- 🖥️ <b>لایهٔ اختیاری VPS</b>: ورودی ثابت برای کلاینت + انتخاب خودکارِ خروجیِ پاک در پس‌زمینه + گذار خودکار با
  نگهبان + مسدودسازی (blackhole) IPv6 برای جلوگیری از نشت. تا ۱۰ اسلات خروجیِ چندکشوری با <b>تخصیص چسبندهٔ
  اسلات↔کشور</b> (بدون جابه‌جایی در هر رفرش) و پذیرش بر اساس <b>کشور خروجیِ واقعیِ اندازه‌گیری‌شده</b>؛ اشتراک به‌صورت
  متمرکز ساخته می‌شود تا با رفرش، پرچم نودها جابه‌جا نشود.
- 🧰 <b>ابزارهای عملیاتی</b>: راهنمای کامل ۱۰ مرحله‌ای دیپلوی، موتور آزمون سرعت سرتاسری (dial-test) و ربات کنترل
  تلگرام (در پوشه‌های <code dir="ltr">docs/</code> و <code dir="ltr">extras/</code>).
- ⚡ پروفایل کیفیت ۶ ساعت در حافظه کش می‌شود و فهرست نودها در لبه کش می‌گردد؛ اگر منبع سنجش کیفیت از کار بیفتد
  به‌صورت نرم تخریب می‌شود و <b>هرگز خروجی نودها را قطع نمی‌کند</b>. بدون نیاز به KV / D1 / متغیر محیطی.

### به‌ارث‌رسیده از پروژهٔ اصلی
- مرورگر نودها: پرچم کشور، میزبان/آی‌پی، امتیاز، نقطهٔ رنگیِ پینگ، نوار سرعت، تعداد کاربران آنلاین، مدت‌زمان
  روشن‌بودن، کاربران/ترافیک تجمعی، اپراتور، توضیح، نشان TCP/UDP؛ جست‌وجو / فیلتر کشوری / مرتب‌سازی / سرستون
  چسبان / نمایش کارتی در صفحه‌های باریک.
- تبدیل دوسویهٔ تک‌نودِ OpenVPN ↔ Mihomo با هشدار سازگاری برای مقادیر نامتعارف (دستگاه tap، رمزهای نادر،
  tls-auth و...)؛ پشتیبانی از خروجی گروهی.

---

## 🧩 نحوهٔ کار

<div dir="ltr">

```
                         ┌──────────────────────────── Cloudflare Worker ───────────────────────────┐
 VPNGate public CSV ────▶│ parse nodes → extract exit IP → IP quality profile (ip-api batch, 6h cache)│
 (www.vpngate.net/api)   │                          → score clean/risk                               │
                         │   GET /            node browser (table with a "Cleanliness" column)       │
                         │   GET /api/servers all nodes as JSON (with a quality field)              │
                         │   GET /sub         filter/sort by params → full Mihomo subscription      │
                         └──────────────────────────────────────────────────────────────────────────┘
                                                              │ clean subscription URL
                                                              ▼
                                              Mihomo / ClashMeta client auto speed-tests
 (Optional) VPS relay layer vps-relay/:
   client ─fixed entry (VLESS/Reality, …)─▶ your VPS ─fwmark policy routing─▶ OpenVPN (tun0/vpnm1-10) ─▶ clean residential IP ─▶ Internet
                                                    ▲
     bestip/multi_refresh refresh pools on a timer (sticky country pinning, official cluster as fallback);
     watchdog accepts by measured exit country / fraud score and rotates when needed;
     sync_subscriptions builds the s-ui subscription centrally (pinned flag emoji, measured-country names, fixed order)
```

</div>

### قواعد امتیازدهی پاکی (<code dir="ltr">assessQuality</code>)

| وزن ریسک | سیگنال |
|---|---|
| +45 | پروکسی عمومی شناخته‌شده (<code dir="ltr">proxy=true</code>) |
| +25 | دیتاسنتر / میزبانی (<code dir="ltr">hosting=true</code>) |
| +6 | نوع کسب‌وکار <code dir="ltr">business</code> |
| +15 | تطبیق ISP/ORG/AS با کلیدواژه‌های میزبانی/VPN |
| −20 (به خط مسکونی واقعی نزدیک‌تر است) | شبکهٔ موبایل (<code dir="ltr">mobile=true</code>) |
| سقف ریسک ۱۰ | ISP مسکونیِ معمولی (نه پروکسی، نه دیتاسنتر) |

<div dir="rtl">

- <code dir="ltr">clean = 100 - risk</code>؛ <code dir="ltr">clean≥80</code> پاک،
  <code dir="ltr">50–79</code> متوسط و <code dir="ltr">&lt;50</code> پرریسک.
- داده از سرویس رایگان batch وب‌سایت <a href="http://ip-api.com">ip-api.com</a> می‌آید (فقط http، ۱۵ درخواست در
  دقیقه، حداکثر ۱۰۰ آی‌پی در هر فراخوانی؛ ورکر درخواست‌ها را دسته‌بندی و کش می‌کند).
- <b>خوشهٔ رسمی ژاپنِ VPN Gate (یعنی <code dir="ltr">219.100.37.x</code> با ASN پژوهشی SoftEther) تقریباً همگی
  <code dir="ltr">proxy=true</code> است</b>؛ بسیار پرسرعت است اما یک پروکسی عمومیِ شناخته‌شده محسوب می‌شود و
  وب‌سایت‌های ضدرisk آن را پرریسک تشخیص می‌دهند. در مقابل، شمار اندکی از نودهای مردمیِ مسکونی/اپراتوری معمولاً
  «پاک‌تر»اند اما سرعت و پایداری متغیر دارند. این پروژه با راهبرد «پاک در اولویت + خوشهٔ رسمی به‌عنوان پشتیبان»
  میان این دو تعادل برقرار می‌کند.

</div>

---

## 🚀 شروع سریع (فقط با ورکر، ۵ دقیقه)

### روش الف: چسباندن در داشبورد (بدون وابستگی)
۱. وارد <a href="https://dash.cloudflare.com/">Cloudflare Dashboard</a> شوید ← Workers & Pages ← ساخت یک Worker.

۲. کل محتوای <code dir="ltr"><a href="worker/worker.js">worker/worker.js</a></code> را در ویرایشگر آنلاین بچسبانید و
دیپلوی کنید.

۳. آدرس <code dir="ltr">*.workers.dev</code> تخصیص‌یافته را باز کنید تا مرورگر نودها نمایش داده شود.

### روش ب: Wrangler CLI

<div dir="ltr">

```bash
cd worker
cp wrangler.toml.example wrangler.toml   # optionally change name / custom domain
npm i -g wrangler && wrangler login
wrangler deploy
```

</div>

### گرفتن آدرس اشتراک (هستهٔ Mihomo / ClashMeta)

<div dir="ltr">

```text
# Speed-first (default; cleanliness/risk annotated per node in comments)
https://your-domain/sub
# Clean-first: Japan only, clean residential IPs, sorted by cleanliness (recommended)
https://your-domain/sub?clean=1&cc=JP&sort=clean
```

</div>

<div dir="rtl">
این آدرس را به‌عنوان «اشتراک» در هر کلاینت با هستهٔ Mihomo وارد کنید (Mihomo Party، Clash Verge Rev، FlClash،
NekoBox و...).
</div>

---

## 🔌 پارامترهای اشتراک (<code dir="ltr">/sub</code>؛ نام‌های مستعار <code dir="ltr">/mihomo</code>، <code dir="ltr">/clash</code>، <code dir="ltr">/subscribe</code>)

| پارامتر | مقادیر | پیش‌فرض | توضیح |
|---|---|---|---|
| `n` | 1–30 | 8 | تعداد نودها |
| `cc` | کد دوحرفی کشور | همهٔ جهان | مثل `JP` / `KR` / `US` |
| `proto` | `tcp`/`udp`/`any` | `tcp` | پروتکل انتقال OpenVPN؛ tcp پایدارتر است |
| `sort` | `score`/`speed`/`ping`/`clean` | `score` | ترتیب مرتب‌سازی؛ `clean` = پاک‌ترین در ابتدا |
| `min` | Mbps | 3 | حداقل آستانهٔ سرعت |
| `clean` | `1` | خاموش | حالت پاک: نه پروکسی/نه دیتاسنتر و clean≥50 |
| `maxrisk` | 0–100 | نامحدود | سقف امتیاز ریسک، مثل `maxrisk=30` |
| `name` | رشته | VPNGate | نام گروه انتخابگر |
| `refresh` | `1` | – | دور زدن کش و دریافت دوباره از بالادست |

<div dir="rtl">
نمونه — فقط ژاپن، ۱۰ نود، ریسک حداکثر ۳۰، مرتب بر اساس پاکی:
</div>

<div dir="ltr">

```text
https://your-domain/sub?cc=JP&n=10&maxrisk=30&sort=clean
```

</div>

### API
<div dir="rtl">
<ul dir="rtl">
<li><code dir="ltr">GET /api/servers</code>: همهٔ نودها به‌صورت JSON؛ هر نود فیلد
<code dir="ltr">quality: {clean,risk,grade,proxy,hosting,mobile,isp,...}</code> دارد و از
<code dir="ltr">?refresh=1</code> پشتیبانی می‌کند.</li>
</ul>
</div>

---

## 🖥️ (اختیاری) لایهٔ تبدیل VPS: ورودی ثابت + انتخاب خودکار خروجی پاک

<div dir="rtl">
برای سناریویی که می‌خواهید کانفیگ کلاینت هیچ‌وقت تغییر نکند و سرور در پس‌زمینه بی‌سروصدا خروجی‌های کم‌ریسک را
انتخاب و جابه‌جا کند. <b>راهنمای کامل ۱۰ مرحله‌ای: <a href="docs/deploy-vps.md"><code dir="ltr">docs/deploy-vps.md</code></a></b>
و <a href="vps-relay/README.md"><code dir="ltr">vps-relay/README.md</code></a> (به زبان انگلیسی/چینی). این لایه:
</div>

<div dir="rtl">
<ul dir="rtl">
<li>به‌صورت دوره‌ای فهرست نودِ دارای امتیاز پاکی را از ورکر شما می‌گیرد و یک استخر کاندید <b>پاک‌دراولویت</b> می‌سازد و خوشهٔ رسمی را به‌عنوان پشتیبان در انتها نگه می‌دارد (هرگز کاملاً قطع نمی‌شود)؛</li>
<li>یک تونل OpenVPN از نوع <code dir="ltr">tun</code> به‌همراه مسیریابی مبتنی بر سیاست با <code dir="ltr">fwmark</code> برپا می‌کند تا فقط ترافیک ورودیِ تعیین‌شده از تونل عبور کند و سایر سرویس‌ها دست‌نخورده بمانند؛</li>
<li>نگهبان هر ۷۵/۱۲۰ ثانیه سلامت را بررسی می‌کند و تنها پس از <b>۲ شکست پیاپی</b> (با زمان خنک‌شدن ۶۰۰ ثانیه‌ای برای هر اسلات) به‌نرمی سوییچ می‌کند تا از قطعی‌های دوره‌ای ناشی از نوسان جدول جلوگیری شود؛</li>
<li>تا ۱۰ تونل مستقلِ چندکشوری (هرکدام fwmark/جدول مسیریابی/دستگاه tun جدا) با <b>تخصیص چسبندهٔ اسلات↔کشور</b>؛ نگهبان اسلات را تنها زمانی سالم می‌داند که <b>کشور خروجیِ اندازه‌گیری‌شده</b> با کشور تخصیص‌یافته برابر باشد (در VPNGate رله‌های زنجیره‌ای‌ای هست که کشور ورود و خروجشان یکی نیست) و در غیر این صورت تعویض می‌کند؛</li>
<li>پایداری اشتراک با <code dir="ltr">sync_subscriptions.py</code>: برای نودهای اصلی ایموجی پرچم ثابت گذاشته می‌شود (پایگاه‌های GeoIP روی محل سرور اختلاف دارند و باعث پرش پرچم می‌شوند)، اسلات‌ها بر اساس کشور خروجیِ واقعی نام‌گذاری می‌شوند و ترتیب اشتراک ادغام‌شده همیشه ثابت است؛</li>
<li>مسدودسازی IPv6، rp_filter سست (loose) و تشخیص نشت از خط اصلی را اعمال می‌کند (اگر خروجی تونل برابر آی‌پی کارت شبکهٔ اصلی باشد، ناسالم تلقی می‌شود)؛</li>
<li>اختیاری: <b>ربات کنترل تلگرام</b> (<code dir="ltr">extras/tgbot/</code>) و <b>موتور آزمون سرعت سرتاسری</b> (<code dir="ltr">extras/dialtest.py</code>).</li>
</ul>
</div>

---

## 📁 ساختار مخزن

<div dir="ltr">

```
.
├── worker/
│   ├── worker.js                 # Single-file Worker (browser + API + subscription + quality engine)
│   └── wrangler.toml.example
├── vps-relay/                    # Optional: VPS auto-picker + fixed-entry relay layer
│   ├── bestip_refresh.py/.sh     # JP-slot clean picker (Worker first, official CSV fallback)
│   ├── build_running.sh          # Builds running.ovpn from candidates (self-bootstraps if missing)
│   ├── ovpn-up.sh / ovpn-down.sh # Policy routing up/down (v4 + v6)
│   ├── watchdog.sh / pick_strict.sh   # JP-slot health check / strict pick-and-switch
│   ├── multi_refresh.py          # Multi-country slot refresh with sticky slot→country pinning
│   ├── multi_build.sh / multi_watchdog.sh  # Per-slot assembly / measured-exit watchdog
│   ├── gen_sbexit.py / gen_keys.sh        # Exit-instance config / Reality keys & certs
│   ├── sync_subscriptions.py     # Single source of truth for s-ui subs: pinned flags, measured names, fixed order
│   ├── merge_subs.py / crontab.example    # cron fallback wrapper
│   ├── vpngate.env.example       # Runtime config template (country / clean mode / Worker URL)
│   ├── nftables.conf             # Firewall template
│   ├── systemd/  sysctl/         # service/timer units / rp_filter example
├── examples/                     # Server-side config templates (placeholders)
├── extras/
│   ├── dialtest.py               # End-to-end real dial-test engine (params via env vars)
│   └── tgbot/                    # Telegram control bot (hard owner allowlist)
├── docs/deploy-vps.md            # Full 10-step VPS deployment guide + pitfalls
├── assets/                       # README screenshots
├── LICENSE
└── README.md / README_EN.md / README_FA.md
```

</div>

---

## 🙏 سپاسگزاری
<div dir="rtl">
<ul dir="rtl">
<li><b><a href="https://github.com/RememberOurPromise/OpenVPNGate4Mihomo">RememberOurPromise/OpenVPNGate4Mihomo</a></b> — نقطهٔ آغاز این پروژه. منطق دریافت/پارس ورکر، نگاشت فیلدهای OpenVPN→Mihomo و ظاهر مرورگر نودِ آن در اینجا بازاستفاده و گسترش یافته است (تحت مجوز Unlicense / مالکیت عمومی).</li>
<li><b><a href="https://www.vpngate.net">آزمایش دانشگاهی VPN Gate</a></b>، پروژهٔ عمومی و خیرخواهانهٔ VPN دانشگاه تسوکوبای ژاپن که نودهایش را داوطلبان سراسر جهان فراهم می‌کنند.</li>
<li><b><a href="https://github.com/MetaCubeX/mihomo">Mihomo (Clash.Meta)</a></b> و <a href="https://wiki.metacubex.one/config/proxies/openvpn/">مستندات OpenVPN</a> آن.</li>
<li>دادهٔ پروفایل آی‌پی از <a href="https://ip-api.com">ip-api.com</a>.</li>
</ul>
</div>

## 👥 نویسندگان (Authors)
<div dir="rtl">
این پروژه به‌صورت مشترک توسط
<a href="https://github.com/ZJH233-tech"><b>@ZJH233-tech</b></a> و <b>Doubao AI</b>
طراحی، توسعه و اشکال‌زدایی شده است.
</div>

## ⚠️ سلب مسئولیت
<div dir="rtl">
<ul dir="rtl">
<li>VPN Gate یک آزمایش دانشگاهی است؛ نودهای عمومی را داوطلبان شخص‌ثالث فراهم می‌کنند و <b>در دسترس‌بودن، سرعت و پاکیِ آن‌ها مدام در نوسان است</b>؛ ریسک استفاده بر عهدهٔ خود شماست.</li>
<li>«امتیاز پاکی/ریسک» صرفاً یک ارزیابی ابتکاری بر پایهٔ سیگنال‌های نوع آی‌پیِ در دسترس عموم است و <b>هیچ تضمینی برای امنیت، قانونی‌بودن یا ناشناس‌ماندن نود نیست</b>؛ حتی با امتیاز پاک هم <b>نباید</b> از طریق رلهٔ عمومی وارد حساب‌های حساس پرداخت یا بانکی شوید.</li>
<li>لطفاً قوانین کشور/منطقهٔ خود و محل نود خروجی را رعایت کنید؛ این پروژه صرفاً برای یادگیری و پژوهش شبکه است.</li>
<li>این پروژه هیچ رابطهٔ رسمی با VPN Gate، Cloudflare، ip-api یا Mihomo ندارد.</li>
</ul>
</div>

## 📄 مجوز (License)
<div dir="rtl">
<a href="./LICENSE">MIT</a>. مشتق از یک پروژهٔ با مجوز Unlicense (مالکیت عمومی)؛ بخش مشتق‌شده تحت MIT منتشر می‌شود.
</div>

</div>
