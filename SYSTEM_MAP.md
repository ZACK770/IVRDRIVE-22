# מפת מערכת IVRDRIVE-22 — עץ שלוחות, אנדפוינטים ופערים

מסמך זה מסכם את כל תתי-המערכת, הזרימות והאנדפוינטים הקיימים והחסרים בקוד הנוכחי (ענף `devin/1787150300-bot-config`).

## 1. שתי זרימות שיחה

המערכת מכילה שני מנועי טלפוניה נפרדים:

| זרימה | כתובת PBX שצריך להגדיר | טכנולוגיה | מתי היא פעילה |
|---|---|---|---|
| **IVR Module API** | `https://ivr-driver.onrender.com/ivr/driver`  
`https://ivr-driver.onrender.com/ivr/passenger`  
`https://ivr-driver.onrender.com/ivr/rating` | FastAPI מחזיר JSON של מודול PBX (`simpleMessage`/`simpleMenu`/`getDTMF`/`simpleRouting`/`hangup`) | שיחות נכנסות/חזרה למספרי אזורים/דירוג |
| **AI Raw Channel** | `wss://ivr-driver.onrender.com/ws/ivr` | WebSocket + Gemini Live (`GeminiLiveSession` + `CallBridge`) | שיחות שמופנות לערוץ הגלם (raw) של ה-PBX |

> **הערת סנכרון:** כיום רוב הלוגיקה העסקית ממומשת ב-**IVR Module API**. ה-AI Raw Channel קיים ועובד, אך הוא לא מחובר לשלוחות ה-PBX הסטנדרטיות (`/ivr/*`) — שם הדialog הוא DTMF ולא Gemini. אם בעתיד כל השיחות יעברו דרך Gemini, יש להזרים את קריאות ה-PBX אל `/ws/ivr` ולא אל `/ivr/passenger`.

---

## 2. עץ השלוחות (IVR Module API)

### 2.1 נהג — `GET/POST /ivr/driver`

כתובת המודול שיש להגדיר ב-PBX:  
`https://ivr-driver.onrender.com/ivr/driver`

```text
start
├── אין נהג רשום               → register_start  → 1 → car_year → seats → birth_year → area → סיום רישום (pending)
├── נהג לא active              → "הרישום ממתין לאישור" → hangup
├── יש נסיעה שהנהג זכה בה      → connect → העברה לטלפון הנוסע (simpleRouting)
├── יש מכרז פתוח באזור         → offer → הקש 1 → await_result → connect/תפוס
└── אחרת                       → driver_menu (1-6)
    ├── 1 — הצעת נסיעה נוכחית באזור אחרון
    ├── 2 — שמיעת מוניטין
    ├── 3 — ניהול אזורים מועדפים (הוספה/הסרה/שמיעה)
    ├── 4 — הגדרת שעות שקט (from → to)
    ├── 5 — עדכון מיקום (פעם ביממה)
    └── 6 — סימון נסיעה כבוצעה
```

שלבי רישום הנהג בטלפון:

| שלב | מה ה-PBX שולח | איך המערכת מגיבה |
|---|---|---|
| `register_start` | מודול `driver_register` + הקש 1 | `_ask_car_year` |
| `register_car_year` | 4 ספרות | `_ask_seats` |
| `register_seats` | 1 ספרה | `_ask_birth_year` |
| `register_birth_year` | 4 ספרות | בחירת אזור (`register_area`) או סיום |
| `register_area` | ספרת אזור | `_finish_registration` → `driver_pending` |

### 2.2 נוסע — `GET/POST /ivr/passenger`

כתובת המודול:  
`https://ivr-driver.onrender.com/ivr/passenger`

```text
start
├── קודם כל מאשר שיוך רפרל (אם יש) → confirm_by_call
└── passenger_menu (1-4)
    ├── 1 — בדיקת יתרת קרדיטים → מקריאה את היתרה בקול → hangup
    ├── 2 — מימוש נסיעה חינם → redeem_ride
    ├── 3 — שתפו וסעו       → הקש מספר → flash_call לאישור → hangup
    └── 4 — עדכון העדפות    → hangup (כרגע רק הודעה)
```

> **שים לב:** במסלול זה אין הזמנת נסיעה חדשה. הזמנה חדשה מתבצעת רק דרך ה-AI Raw Channel (`/ws/ivr`) או דרך הקונסולה (`POST /api/orders`).

### 2.3 דירוג — `GET/POST /ivr/rating`

כתובת המודול:  
`https://ivr-driver.onrender.com/ivr/rating`

```text
start → rating_prompt (1-5) → record_score → "תודה" → hangup
```

האנדפוינט קורא ל-`ratings.record_score(score)` ומסיים את השיחה.

---

## 3. אנדפוינטי REST

### 3.1 ממשק המפעיל/קונסולה — `app/ops_api.py` (prefix `/api`)

| שיטה | נתיב | מטרה |
|---|---|---|
| `GET` | `/api/drivers` | רשימת נהגים (אופציה `?status=...`) |
| `POST` | `/api/drivers` | יצירת/עדכון נהג (משמש גם עריכה) |
| `PATCH` | `/api/drivers/{id}` | עדכון נהג |
| `DELETE` | `/api/drivers/{id}` | **מושעה** (`status = "suspended"`) — לא מחיקה |
| `POST` | `/api/drivers/{id}/location` | דיווח מיקום ידני |
| `POST` | `/api/drivers/{id}/flash` | שליחת צינתוק ידני לנהג |
| `GET` | `/api/drivers/board` | לוח אזורים פעילים עם נהגים זמינים |
| `GET` | `/api/areas` | רשימת אזורים |
| `POST` | `/api/areas` | יצירת/עדכון אזור (`name`, `callback_number`, `flash_cid`, `active`) |
| `POST` | `/api/orders/{id}/tender` | פתיחת מכרז להזמנה |
| `GET` | `/api/tenders` | רשימת מכרזים פתוחים/סגורים |
| `GET` | `/api/tenders/{id}` | פרטי מכרז + הצעות + רשימת חיוגים |
| `POST` | `/api/tenders/{id}/bid` | ביצוע הצעה ידנית |
| `POST` | `/api/tenders/{id}/close` | סגירת מכרז ובחירת זוכה |
| `POST` | `/api/tenders/{id}/cancel` | ביטול מכרז |
| `POST` | `/api/orders` | יצירת הזמנה ידנית |
| `POST` | `/api/orders/{id}/finish` | סיום נסיעה וחישוב ניקוד/דירוג |
| `POST` | `/api/orders/{id}/cancel` | ביטול הזמנה והחזרת קרדיטים |
| `POST` | `/api/orders/{id}/redeem` | מימוש קרדיטים להזמנה |
| `GET` | `/api/club/members` | רשימת חברי מועדון |
| `GET` | `/api/club/{phone}` | פרטי נוסע + קרדיטים |
| `POST` | `/api/club/{phone}/adjust` | עדכון ידני של קרדיטים |
| `PATCH` | `/api/club/{phone}/preferences` | עדכון העדפות נוסע |
| `GET` | `/api/referrals` | רשימת שיוכים |
| `POST` | `/api/referrals` | יצירת שיוך ידני |
| `GET` | `/api/ratings` | רשימת בקשות דירוג |
| `POST` | `/api/ratings/{id}/call` | חיוג ידני לדירוג |
| `POST` | `/api/ratings/{id}/score` | שמירת ציון דירוג ידני |
| `GET` | `/api/accounting/summary` | דוח P&L |
| `GET` | `/api/accounting/drivers` | דוח נסיעות לנהגים |
| `GET` | `/api/accounting/drivers/{id}` | פירוט נהג |
| `POST` | `/api/accounting/drivers/{id}/send` | שליחת פירוט לנהג |
| `GET` | `/api/expenses` | רשימת הוצאות |
| `POST` | `/api/expenses` | הוספת הוצאה |
| `GET` | `/api/logs` | יומני פעולות |
| `GET` | `/api/settings` | קבלת הגדרות מערכת |
| `PUT` | `/api/settings` | עדכון הגדרות מערכת |

### 3.2 ממשק כללי / בוט — `app/api.py` (prefix `/api`)

| שיטה | נתיב | מטרה |
|---|---|---|
| `GET` | `/api/orders` | רשימת הזמנות |
| `PATCH` | `/api/orders/{id}` | עדכון סטטוס/שדות הזמנה |
| `GET` | `/api/summary` | מספרים ראשיים למסך מפעיל |
| `GET` | `/api/calls` | יומן שיחות |
| `GET` | `/api/calls/{pk}` | פרטי שיחה (טראנסקריפט, סטטיסטיקה) |
| `GET/PUT/POST` | `/api/botconfig` / `/api/botconfig/reset` | קריאה, עדכון ואיפוס BotConfig |
| `GET` | `/api/customers` | רשימת לקוחות |
| `GET/PUT/POST` | `/api/prompt` / `/api/prompt/reset` | קריאה/עדכון/איפוס פרומפט שנוצר מ-BotConfig |

### 3.3 ניהול דפדפני ישן — `app/admin.py` (prefix `/admin`)

| שיטה | נתיב | מטרה |
|---|---|---|
| `GET/POST` | `/admin` / `/admin/prompt` | עריכת פרומפט HTML פשוט |
| `GET/POST` | `/admin/customers` | צפייה/יצירת לקוחות |
| `POST` | `/admin/customers/{id}/delete` | מחיקת לקוח |
| `GET` | `/admin/calls` | רשימת שיחות |
| `GET` | `/admin/calls/{pk}` | פרטי שיחה |
| `GET` | `/admin/orders` | רשימת הזמנות |
| `GET` | `/admin/orders.xlsx` | ייצוא הזמנות לאקסל |

### 3.4 שירותים שונים ב-root — `app/main.py`

| שיטה | נתיב | מטרה |
|---|---|---|
| `GET` | `/healthz` | סטטוס בריאות + מצב dry-run |
| `GET` | `/outbound-ip` | מחזיר את כתובת ה-IP היוצאת של השרת |
| `GET` | `/audio/{file_name}` | הגשת קובץ אודיו מהתיקיה `audio/` |
| `GET` | `/api/captures` | רשימת הקלטות שיחות raw |
| `GET` | `/api/captures/{call_id}` | פריימים של הקלטה |
| `GET` | `/captures/{call_id}/{filename}` | הורדת קובץ הקלטה |
| `GET` | `/` | דף סיכום הקלטות raw |
| `GET` | `/call/{call_id}` | דף פרטי הקלטה raw |
| `GET` | `/console` / `/console/{path:path}` | proxy לקונסולה React המתארחת בשירות נפרד |
| `WS` | `/ws/ivr` | ערוץ WebSocket ל-AI / Gemini Live |
| `WS` | `/ws/{rest:path}` | WebSocket catch-all |
| `GET/POST` | `/ws/ivr` | probe HTTP לבדיקת הגעת PBX |

### 3.5 קריאות החוצה ל-PBX (Technoline Interaction API)

השרת שלנו קורא לכתובות האלה. לכולן נדרש `PBX_API_KEY` ו-IP whitelist:

```text
https://app.ipsales.co.il/ivrFilesApi.php?action=makeCall&phone=...&cid=...&apiKey=...
https://app.ipsales.co.il/campaignApi.php
  (POST JSON: {action:"campaignRun", apiKey, campaignName, phones[], messagesType:"apiUrl", callLength, ...})
https://app.ipsales.co.il/campaignApi.php
  (POST JSON: {action:"campaignReport", apiKey, campaignId})
https://app.ipsales.co.il/campaignApi.php
  (POST JSON: {action:"campaignStop", apiKey, campaignId})
https://app.ipsales.co.il/ivrFilesApi.php
  (POST multipart: {action:"uploadFile", apiKey, fileName}, file)
```

פונקציות ה-python שמבצעות את הקריאות:

- `pbx.flash_call()` — צינתוק (`makeCall`).
- `pbx.voice_broadcast()` — קמפיין קולי (`campaignRun`).
- `pbx.campaign_report()` / `pbx.stop_campaign()` — מעקב אחרי קמפיינים.
- `pbx.upload_file()` — העלאת קבצי אודיו.

---

## 4. מודולים פנימיים עיקריים

| קובץ | תפקיד | פונקציות/מחלקות מרכזיות |
|---|---|---|
| `app/db.py` | מודלים, הגדרות, BotConfig | `Customer`, `Order`, `Driver`, `DriverArea`, `Area`, `Tender`, `TenderBid`, `Referral`, `RatingRequest`, `PointsEntry`, `Expense`, `Setting`, `BotConfig`, `CallLog`, `FlashCall`, `LocationUpdate`; `DEFAULT_BOTCONFIG`, `DEFAULT_SETTINGS` |
| `app/ivr.py` | לוגיקת ה-PBX Module API | `driver_line`, `_driver_step`, `passenger_line`, `_passenger_step`, `rating_line`, `_rating_step`, `message`, `menu`, `ask_digits`, `route`, `hangup` |
| `app/bridge.py` | WebSocket ↔ Gemini Live | `CallBridge` (`feed`, `_pump_input`, `_pump_output`, `_pump_model`, `_handle_transfer`, `run`, `finish`) |
| `app/gemini_live.py` | לקוח Gemini | `GeminiLiveSession` (`send_audio`, `send_text`, `send_tool_responses`, `events`) |
| `app/tools.py` | כלים ש-Gemini קורא להם | `ToolContext`, `get_customer`, `get_recent_call`, `get_points`, `save_order`, `hangup_call`, `transfer_to_representative`, `redeem_order`, `create_referral` |
| `app/dispatch.py` | מנוע המכרזים | `open_tender`, `blast_tender`, `place_bid`, `close_tender`, `reap`, `cancel`, `finish_ride`, `voice_module_url` |
| `app/drivers.py` | ניהול נהגים וניקוד נהג | `register`, `add_area`, `remove_area`, `candidates`, `total_score`, `general_score`, `situational_score`, `tier_of`, `matches_filters`, `report_location`, `area_board` |
| `app/loyalty.py` | מועדון נוסעים וניקוד | `balance`, `history`, `grant`, `award_for_order`, `reverse_for_order`, `can_redeem`, `redeem_ride`, `adjust`, `club_members` |
| `app/referrals.py` | שתפו וסעו | `assign`, `confirm_by_call`, `credit_for_order`, `expire_stale` |
| `app/ratings.py` | דירוגי נהג | `schedule_for_order`, `due_requests`, `place_call`, `record_score`, `run_due` |
| `app/pbx.py` | לקוח Technoline | `flash_call`, `voice_broadcast`, `campaign_report`, `stop_campaign`, `upload_file` |
| `app/scheduler.py` | לולאת רקע | `tick`, `monitor_voice_campaigns`, `start`, `stop` |
| `app/accounting.py` | דוחות כספיים | `profit_and_loss`, `driver_statement`, `rides_by_driver`, `add_expense` |
| `app/tts.py` | טקסטים קוליים | `AUDIO_TEXTS`, `offer_text`, `area_menu_text`, `registration_text` |
| `app/notify.py` | הודעות טקסט | `send_text`, `send_order` |
| `app/cost.py` | מדידת עלות Gemini | `UsageMeter` |
| `app/capture.py` | הקלטת raw channel | `CallCapture`, `list_captures`, `load_capture`, `load_frames` |
| `app/codecs.py` | קידוד אודיו | `mulaw`/`alaw`/`pcm16le`/`pcm16be` |
| `app/console_proxy.py` | proxy לקונסולה | `/console/*` |

---

## 5. מודל BotConfig המרכזי

כל הפרומפט/חוקים/מחירים/שאלון/פעולות מרוכזים בעמודה אחת ב-`bot_config.config`:

| שדה | משמעות |
|---|---|
| `name` | שם המוקד |
| `identity` | מי הבוט ותפקידו |
| `iron_rules` | חוקים קשיחים |
| `guidelines` | סגנון דיבור |
| `opening_sentence` | משפט פתיחה |
| `knowledge` | ידע חופשי (מחירים, אזורים, כללים) |
| `language`, `voice` | שפה וקול Gemini |
| `representative_phone` | מספר להעברה לנציג |
| `allowed_actions` | רשימת פעולות מותרות לבוט (`hangup_call`, `transfer_to_representative`, `save_order`, `get_recent_call`) |
| `questionnaire` | מערך שאלות עם `id`, `question`, `instructions` |
| `q_and_a` | שאלות ותשובות נוספות |

הניהול נעשה בקונסולה דרך `GET/PUT /api/botconfig` ודף "עריכת בוט".

---

## 6. פערים ודברים שעדיין אין להם קוד

### 6.1 פערי תשתית / הגדרות (לא קוד, חייבים לסגור מיד)

1. **מפתח PBX ו-IP whitelist** — `PBX_API_KEY` לא מוגדר ב-Render, וכתובת ה-IP היוצאת של השרת עדיין לא ברשימת ה-whitelist של Technoline. בפועל `pbx.DRY_RUN=True` ושום צינתוק/קמפיין אמיתי לא יוצא.
2. **כתובת IP קבועה ב-Render** — ללא proxy/static IP, כתובת ה-outbound משתנה. יש כבר `/outbound-ip` לבדיקה, אבל עדיין צריך לבחור פתרון (Static IP Relay / Fixie / Render Static Outbound IP).
3. **`GEMINI_API_KEY`** — אם רוצים שיחות AI חיות, המפתח צריך להיות בסביבה.
4. **`ADMIN_TOKEN`** — הקונסולה וה-ops API פתוחים אם לא מוגדר.

### 6.2 פערי תפקוד בקוד

5. **אין העברה לנציג בנתיב IVR (`/ivr/*`)** — רק ה-AI Raw Channel (`/ws/ivr`) תומך בהעברה דרך `transfer_to_representative`. אם השיחות הנכנסות עוברות דרך המודולים הרגילים, אין אפשרות להעביר ל-`representative_phone` או לנתק אוטומטית.
6. **אין הזמנה חדשה דרך `/ivr/passenger`** — הנוסע יכול רק לבדוק קרדיטים, לממש, לשייך חבר ולעדכון העדפות. ליצירת הזמנה חדשה דרך טלפון צריך את `/ws/ivr` + Gemini.
7. **אין ניהול קבצי אודיו דרך API/UI** — `pbx.upload_file()` ו-`tts.synthesize()` קיימים אך ניתנים לשימוש רק מסקריפטים (`tools/`). אין אנדפוינט להעלות/לנהל את הקבצים ב-PBX.
8. **אין אנדפוינט לפרטי `Order` בודד** — יש `GET /api/orders` ו-`PATCH /api/orders/{id}` אבל לא `GET /api/orders/{id}`; הקונסולה עובדת ברשימה.
9. **אין אנדפוינט לדוח הקמפיין/צינתוקים** — `pbx.campaign_report()` רץ רק מה-scheduler. אין דרך למפעיל לראות את מצב הקמפיין או את יומן `FlashCall`.
10. **אין מימוש העדפות נוסע בטלפון** — `/ivr/passenger` dtmf=4 רק אומר "ההעדפות נשמרו" אך לא מקליט/משמיע אותן.
11. **אין יכולת "חזרה"/ביטול בתוך תפריטי IVR** — בכל שלב שגיאה מובילה להודעת שגיאה או חזרה לתפריט הראשי, אין "לחצן * לחזור".
12. **ה-AI Raw Channel לא חושף את `BotConfig` לשיחות IVR** — כל עוד `/ivr/passenger` לא עובר דרך Gemini, השדות `questionnaire` ו-`allowed_actions` לא משפיעים על הזמנה טלפונית.

### 6.3 פערים בהגדרות עסקיות

13. **מחירון** — ישות `Price` הוסרה; המחירים עברו לשדה `knowledge` ב-BotConfig. ברירת המחדל של `DEFAULT_BOTCONFIG["knowledge"]` כבר מכילה ~270 מחירים ששודרגו מאתר driverim.online; הבוט משתמש בהם דרך `lookup_price`. עדיין אין מנוע שמגלה מחיר אוטומטית שלא ברשימה.
14. **לא מוגדרים webhooks מרכזיה** — המערכת מניחה שה-PBX יחזור למודולים שלנו אך אין אימות חתימה או endpoint נפרד לסטטוס שיחה/קמפיין.

---

## 7. סנכרון מציאות ↔ מסמך זה

| תת-מערכת | סטטוס |
|---|---|
| BotConfig מרכזי | מומש (`/api/botconfig`, `web/src/BotConfig.tsx`), ישות `Price` נמחקה |
| צינתוק לזוכה במכרז | מומש ב-`dispatch.open_tender` → `pbx.flash_call`/`voice_broadcast` אך רץ ב-dry-run |
| רישום נהג בטלפון | מומש ב-`/ivr/driver` |
| רישום נהג בקונסולה | מומש ב-`/api/drivers` + `web/src/Drivers.tsx` |
| העברה לנציג | קיימת רק ב-AI bridge, לא ב-IVR Module |
| שיוך נהגים ל"שתפו וסעו" | תוקן — `referrals._known_number` מאפשר שיוך לנהג שלא היה נוסע |
| הסרת נהג | תוקנה ל-`suspended`; UI מציג "מושעה" |
| דירוג אוטומטי | מומש ב-`ratings.schedule_for_order` + scheduler |
| מועדון נוסעים | מומש ב-`loyalty.py` |
