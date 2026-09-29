# Auth testing playbook — single-admin PIN gate

There is NO email/password user system. One shared PIN protects all write endpoints.

## Model
- PIN is stored bcrypt-hashed in MongoDB: `bot_settings` doc `_id="config"`, field `pin_hash`.
- Seeded on backend startup from `ADMIN_PIN` in `/app/backend/.env` (only if the doc does not exist).
- `POST /api/admin/unlock {pin}` → verifies with `bcrypt.checkpw` → returns a JWT valid 8 hours.
- JWT payload: `{sub: "admin", type: "admin", exp}` signed HS256 with `JWT_SECRET`.
- Protected endpoints use the `require_admin` dependency and expect `Authorization: Bearer <token>`.

## Protected (write) endpoints
- `PUT /api/settings` — body `{official_username, welcome_ta, welcome_en}`
- `POST /api/admin/pin` — body `{new_pin}`

## Public (read) endpoints
- `GET /api/settings`, `GET /api/bot/status`, `GET /api/bot/clicks`, `GET /api/r`

## Brute force
`admin_attempts` collection keyed by client IP. 5 failures → 15 min lockout → HTTP 429.
Successful unlock deletes the attempt doc. Clear manually with
`db.admin_attempts.deleteMany({})`.

## Step 1 — Mongo verification
```
mongosh
use test_database
db.bot_settings.findOne({_id: "config"})          // pin_hash must start with $2b$
db.admin_attempts.find().pretty()
```

## Step 2 — API testing
```bash
API=https://traffic-to-account.preview.emergentagent.com

# public read works with no auth
curl -s $API/api/settings

# wrong pin -> 401 {"detail":"Wrong PIN."}
curl -s -X POST $API/api/admin/unlock -H "Content-Type: application/json" -d '{"pin":"000000"}'

# correct pin -> {"token": "...", "expires_in_hours": 8}
TOKEN=$(curl -s -X POST $API/api/admin/unlock -H "Content-Type: application/json" \
  -d '{"pin":"246810"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# write without token -> 401
curl -s -X PUT $API/api/settings -H "Content-Type: application/json" \
  -d '{"official_username":"someuser","welcome_ta":"a","welcome_en":"b"}'

# write with token -> 200, official_url rebuilt from the username
curl -s -X PUT $API/api/settings -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"official_username":"@someuser","welcome_ta":"தமிழ்","welcome_en":"english"}'
```

## Step 3 — Validation rules to assert
- Username accepted forms: `name`, `@name`, `https://t.me/name`, `t.me/name?text=hi`
- Username rejected: shorter than 5, longer than 32, or containing anything outside `[A-Za-z0-9_]`
  → HTTP 400 with a human-readable detail string
- `official_url` is always rebuilt server-side as `https://t.me/<username>?text=hi`
  (never taken from client input)
- Expired/garbage bearer token → 401 with a friendly "Enter your PIN again." message

## Step 4 — Bot propagation
The bot process caches settings for 5 seconds. After a `PUT /api/settings`, wait >5s; the next
`/start` reply must use the NEW destination URL and the NEW welcome body text.
Verify by reading `bot_settings` and by calling `bot.get_settings()` in the bot process context.
Do NOT DM real users (5859128324, 8354942885) to test this.

## Frontend flow
- `data-testid="lock-chip"` toggles the PIN form (`unlock-form`, `pin-input`, `unlock-submit`).
- Token is kept in `sessionStorage` under `adminToken` and sent as a Bearer header.
- `routes-to-row` click: locked → opens PIN form; unlocked → inline `account-input` + `account-save`.
- `welcome-ta-input` / `welcome-en-input` are `disabled` until unlocked; `config-save` persists them.
- A 401 from any write clears the token and re-opens the PIN form.
