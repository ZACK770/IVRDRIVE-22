# אפיון מערכת IVRDRIVE

מסמך זה מסכם את דרישות המוצר לאפליקציית מוקד ההסעות והנהגים, ומקשר כל דרישה לקובץ/אנדפוינט בפרויקט. הוא מבוסס על האפיון העסקי ובנוסף מתייחס לתיעוד המרכזיה Technoline (`PBX_DOCUMENTATION_CENTER.md`), שמסביר את שני ממשקי ה-API: **Module API** (המערכת עונה ל-PBX במהלך שיחה) ו-**Interaction API** (השרת קורא ל-PBX כדי ליזום שיחות/קמפיינים).

---

## 1. חוויית נוסע

### 1.1 צבירת ניקוד (מועדון נוסעים)

כל נסיעה שמסתיימת מזכה את מספר הטלפון בניקוד לפי סכום הנסיעה. לא מוענק ניקוד על נסיעות שלא בוצעו, וניתן לממש נקודות לנסיעת חינם (תשלום בניקוד). מתנה חד-פעמית של ניקוד ראשון מוענקת בנסיעה הראשונה.

**מימוש:**

- מודל `PointsEntry` (`app/db.py`) — לדבר אappend-only; כל תנועה היא שורה חדשה.
- `loyalty.award_for_order()` (`app/loyalty.py`) — מעניק ניקוד סיום, מתנת הצטרפות (`first_ride_gift`), והטבת שתפו וסעו.
- `loyalty.redeem_ride()` — משמש נסיעה חינם תמורת ניקוד.
- קונסולה: `GET /api/club/members`, `GET /api/club/{phone}`, `POST /api/club/{phone}/adjust` (`app/ops_api.py`).

### 1.2 "שתפו וסעו" (הזמכות)

מזמין מקשר מספר חדש למספר שלו. אם המספר אינו כבר בשיוך, נשמר הקשר; למספר המשויך יכול להתבצע צינתוק לאישור תוך 24 שעות. הזיכוי על נסיעות ממספר זה נמשך 30 יום ממועד השיוך. אותו מספר לא ניתן לשייך פעמיים.

**מימוש:**

- מודל `Referral` (`app/db.py`).
- `referrals.assign()` ו-`referrals.confirm_by_call()` (`app/referrals.py`).
- שיחת האישור קורית דרך IVR: `GET/POST /ivr/passenger` (`app/ivr.py`) — מקיש 3, מקליד מספר, ומקבל צינתוק אישור.
- קונסולה: `GET /api/referrals`, `POST /api/referrals` (`app/ops_api.py`).

### 1.3 דירוג נהג

לאחר סיום נסיעה נשלחת שיחת דירוג לנוסע — אוטומטית כשעה ומחצה אחרי הנסיעה, או מיד כאשר הנהג מסיים דרך התפריט. וידוא שהדירוג לא נשלח פעמיים.

**מימוש:**

- מודל `RatingRequest` (`app/db.py`).
- `ratings.schedule_for_order()` (`app/ratings.py`).
- `dispatch.finish_ride()` מפעילה את הדירוג בעת סיום נסיעה.
- שיחת דירוג: `GET/POST /ivr/rating` (`app/ivr.py`).
- קונסולה: `GET /api/ratings` (`app/ops_api.py`).

### 1.4 אזור אישי / העדפות

נוסע יכול לנהל העדפות (כתובת ברירת מחדל, נהג מועדף, חסימת נהג, ביטול תפוצה).

**מימוש:**

- מודל `Customer` (`app/db.py`).
- IVR: `GET/POST /ivr/passenger` — תפריט אישי (יתרת נקודות, מימוש, שתפו וסעו, העדפות).
- קונסולה: `PATCH /api/club/{phone}/preferences` (`app/ops_api.py`).

---

## 2. חוויית נהג

### 2.1 צינתוק למכרז נסיעות

בסיום יצירת הזמנה, המערכת פותחת מכרז לאזור המתאים ומפעילה צינתוק/שידור קולי לנהגים זכאים:

- מספרים שאינם משלמים מקבלים צינתוק (flash call) חינם.
- נהגים משלמים מקבלים הודעה קולית מוקלטת; לחיצה על 1 מגישה הצעה ללא צורך בחיוג חזרה.
- נהג שחיוג חזר למספר האזור שומע את פרטי הנסיעה ומקיש 1 כדי להתעניין.
- המערכת נותנת חלון זמן קצר (ברירת מחדל 10 שניות) לנהגים נוספים; בסיום החלון האלגוריתם בוחר את הנהג המועדף ומחבר אותו לנוסע.

**מימוש:**

- `dispatch.open_tender()` ו-`dispatch.close_tender()` (`app/dispatch.py`).
- `drivers.candidates()` ו-`drivers.total_score()` (`app/drivers.py`) — בחירת נהגים זכאים וניקוד.
- `pbx.flash_call()` ו-`pbx.voice_broadcast()` (`app/pbx.py`) — שליחת הצינתוק/שידור דרך Technoline Interaction API.
- IVR הנהגים: `GET/POST /ivr/driver` (`app/ivr.py`) — שלב `offer` (שמיעת הצעה ולחיצת 1), `await_result` (המתנה לסגירת המכרז) והעברה לנוסע.
- קונסולה: `POST /api/orders/{order_id}/tender`, `POST /api/tenders/{tender_id}/bid`, `POST /api/tenders/{tender_id}/close` (`app/ops_api.py`).
- **חיבור לבוט:** `_save_order()` ב-`app/tools.py` מפעילה `dispatch.open_tender()` אוטומטית בסיום שמירת הזמנה (ברירת מחדל `auto_tender=1`).

### 2.2 תפריט נהג מהטלפון

נהג יכול להירשם, לעדכן אזורים מועדפים, שעות שקט, מיקום, ולסמן סיום נסיעה.

**מימוש:**

- `GET/POST /ivr/driver` (`app/ivr.py`):
  - 1 — הצעה נוכחית
  - 2 — מוניטין
  - 3 — אזורים מועדפים
  - 4 — שעות שקט
  - 5 — עדכון מיקום (פעם ביום למקור "declared")
  - 6 — סיום נסיעה
- רישום נהג: `drivers.register()` (`app/drivers.py`).
- קונסולה: `GET/POST /api/drivers`, `POST /api/drivers/{driver_id}/location` (`app/ops_api.py`).

### 2.3 מוניטין ורמות נהג

הנהג מקבל ציון כללי (0–100) המבוסס על דירוגים, גיל ושנת רכב, ותק, גיל הנהג וכמות נסיעות. לפי הציון הוא משויך לרמות: סטנדרט, פרו, פרו פלוס, פרימיום.

**מימוש:**

- `drivers.general_score()`, `drivers.tier_of()` (`app/drivers.py`).
- חישוב סיטואציוני — מיקום עדכני באזור (`drivers.situational_score()`).
- ציוד אודיו של מוניטין דרך `/ivr/driver` dtmf=2.

---

## 3. ניהול ומאחורי הקלעים

### 3.1 אלגוריתמי סלקציה

האלגוריתם שוקל שני רבדים:

1. **רכיב כללי** — דירוג, רכב, ותק, גיל, נסיעות.
2. **רכיב סיטואציוני** — האם הנהג באזור כרגע? עדכון מיקום מסיום נסיעה מהימן יותר מהצהרה עצמית.

האלגוריתם רץ פעמיים: בשלב שליחת הצעות ובשלב סגירת המכרז (כשמגיעות תגובות), כדי לבחור את הנהג המועדף.

**מימוש:**

- `drivers.total_score()`, `drivers.candidates()`, `drivers.matches_filters()` (`app/drivers.py`).
- משקלים והגדרות ניתנים לשינוי בטבלת `settings` (`app/db.py`) — `score_weight_*`, `score_situational_share`, `location_fresh_hours`.
- `dispatch.close_tender()` ממיין הצעות לפי הציון הגבוה ביותר ושובר שיוויון לפי מועד לחיצת 1.

### 3.2 ממשק סדרן / לוח ניהול

- צפייה בהזמנות פתוחות, סגירת מכרז ידנית, מיון וסינון נהגים, קליטת מיקומים.
- ממשק לרואה חשבון: הכנסות, הוצאות, פירוט נסיעות, נסיעות בניקוד, צוברים, שליחת פירוט לנהג.

**מימוש:**

- `app/ops_api.py` — נקודת קצה אחת לניהול: `/api/orders`, `/api/tenders`, `/api/drivers`, `/api/drivers/board`, `/api/areas`, `/api/club/*`, `/api/referrals`, `/api/ratings`.
- `app/accounting.py` — דוחות וחישובי עמלה.
- התצוגה הוויזואלית בקונסולה React (`web/src/*`).

---

## 4. זרימת נתונים מרכזית

```
שיחת לקוח ↔ בוט AI (Gemini Live / bridge) ↔ כלי save_order
     ↓
שמירת Order ב-DB
     ↓
dispatch.open_tender() → בחירת נהגים זכאים → pbx.flash_call() / pbx.voice_broadcast()
     ↓
נהג חיוג חזר למספר אזור → /ivr/driver (Module API) → place_bid()
     ↓
חלון המכרז נסגר (scheduler/ידני) → dispatch.close_tender() → העברה לנוסע + Order assigned
     ↓
סיום נסיעה (IVR / ops API) → dispatch.finish_ride() → ניקוד + דירוג
```

---

## 5. סביבה וקונפיגורציה

משתני סביבה מהותיים לריצה:

- `BOT_DB_URL` — Postgres / SQLite.
- `PUBLIC_BASE_URL` — כתובת שבה ה-PBX מגיע למודולים שלנו (`/ivr/*`).
- `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL` — הבוט.
- `PBX_BASE_URL`, `PBX_API_KEY` — קריאות ל-Technoline Interaction API.
- `PBX_DRY_RUN=1` — במצב פיתוח, לא מבצע צינתוקים אמיתיים.
- `ADMIN_TOKEN` — אימות קונסולה ו-ops API.

---

## 6. מהטבלה לקוד — מיפוי מהיר

| דרישה | קובץ/אנדפוינט |
|---|---|
| צינתוק אוטומטי אחרי שמירת הזמנה | `app/tools.py` `_save_order()` → `dispatch.open_tender()` |
| מכרז, סלקציית נהג, חלון 10 שניות | `app/dispatch.py` |
| ציון נהג ורמות | `app/drivers.py` |
| קריאת צינתוק/שידור קולי ל-PBX | `app/pbx.py` |
| תפריט נהג טלפוני | `GET/POST /ivr/driver` (`app/ivr.py`) |
| מועדון נוסעים וניקוד | `app/loyalty.py`, `/ivr/passenger` |
| שתפו וסעו | `app/referrals.py`, `/ivr/passenger` |
| דירוג נהג אוטומטי | `app/ratings.py`, `/ivr/rating` |
| ניהול דרך קונסולה | `app/ops_api.py`, `web/src/*` |
| הגדרות עסקיות ניתנות לשינוי | `app/db.py` `DEFAULT_SETTINGS` |
