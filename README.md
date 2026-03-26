# 🎲 DnD AI DM Bot - 失落的芬德爾礦坑

A Cantonese-language AI Dungeon Master Telegram bot for **Lost Mine of Phandelver** (DnD 5e).
Powered by **DeepSeek**, **Supabase**, and **python-telegram-bot v20**.

---

## ✨ Features

- 🤖 **AI Dungeon Master** - Full Cantonese DM narration via DeepSeek
- 🗡️ **Combat System** - Emoji grid, initiative tracker, auto monster turns
- 👤 **Character Creation** - Class, race, background, AI-generated stats
- 💾 **Persistent Memory** - Campaign state saved in Supabase
- 📖 **Complete LMOP** - All 6 locations, 15 NPCs, 12 monsters

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| AI | DeepSeek |
| Database | Supabase |
| Bot | python-telegram-bot v20 |

---

## 📋 Prerequisites

- Python 3.10+
- Telegram Account
- DeepSeek API Key
- Supabase Account

---

## 🚀 Quick Setup

### 1. Clone the Project
```bash
git clone https://github.com/YOUR_USERNAME/dnd-dm-bot
cd dnd-dm-bot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Create Environment File
```bash
# Create .env file with your credentials
cp .env.example .env
# OR manually create:
touch .env
```

Edit `.env` with your credentials:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DEEPSEEK_API_KEY=your_deepseek_api_key
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_supabase_service_key
```

---

## 🔧 Getting Your API Keys

## 🔧 Getting Your API Keys

### Step 1: Create Telegram Bot

#### Using BotFather
1. **Open Telegram** → Search for **@BotFather**
2. **Start the bot** → Type `/start`
3. **Create new bot** → Type `/newbot`
4. **Choose a name** (e.g., "DnD Adventure Bot")
5. **Choose a username** (must end with bot, e.g., `my_dnd_bot`)
6. **Copy the token** - You'll get something like:
   ```
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   ```

#### Enable Bot Features
1. In BotFather, type `/mybots`
2. Select your bot
3. Go to **Bot Settings** → **Menu Button** → **Configure Menu Button**
4. Add a button that links to your bot (optional)

---

### Step 2: DeepSeek API Key
1. Go to https://platform.deepseek.com/
2. Sign up / Login
3. Go to API Keys
4. Create new key

### Supabase Setup
1. Go to https://supabase.com/
2. Create new project
3. Go to Settings → API
4. Copy:
   - Project URL
   - service_role key (not anon key)

---

## 🗄️ Set Up Supabase Database

### Option 1: SQL Editor (Recommended)
1. Open your Supabase project
2. Go to **SQL Editor**
3. Copy and paste the contents of `schema.sql`
4. Click **Run**

### Option 2: Table-by-Table
The schema creates 7 tables:
- `campaigns` - Campaign state
- `characters` - Player characters
- `events` - Story events
- `memory_summaries` - Session summaries
- `world_state` - Decision flags
- `combat_sessions` - Combat state
- `combat_entities` - Combatants

---

## 🎮 Running the Bot

### Local Development
```bash
python main.py
```

---

## 📖 Bot Commands

| Command | Description |
|---------|-------------|
| `/newgame` | Start a new campaign |
| `/newchar` | Create your character |
| `/startadventure` | Begin the adventure |
| `/status` | View character sheets |
| `/recap` | AI session summary |
| `/roll 2d6` | Roll dice |
| `/startcombat goblin 3` | Start combat (3 goblins) |
| `/attack goblin1 18 7` | Attack (roll=18, damage=7) |
| `/combatgrid` | Show emoji battle grid |
| `/nextturn` | Next combat turn |
| `/endcombat` | End combat |

---

## 📁 Project Structure

```
dnd-dm-bot/
├── main.py              # Bot entry point
├── config.py            # Configuration
├── schema.sql           # Database schema
├── requirements.txt     # Python dependencies
├── db/                  # Database layer
│   ├── supabase_client.py
│   ├── campaigns.py
│   ├── characters.py
│   ├── events.py
│   └── combat.py
├── dm/                  # AI DM layer
│   ├── deepseek_client.py
│   ├── context_builder.py
│   ├── memory_manager.py
│   └── module_lmop.py
├── combat/              # Combat system
│   ├── mechanics.py
│   ├── initiative.py
│   └── grid.py
| Dockerfile           # Docker config (optional)
├── fly.toml             # Fly.io config (optional)
└── handlers/            # Telegram handlers
    ├── campaign.py
    ├── character.py
    ├── combat_handlers.py
    └── general.py
```

---

## ⚠️ Important Notes

1. **.env file** - Keep this secret! Never commit to GitHub
2. **Supabase** - Make sure tables are created before running
3. **DeepSeek** - Ensure API key has credits
4. **Telegram** - Bot must be started with /start first

### 🔗 Connect Bot to the App

After setting up your `.env` file:

1. **Edit the .env file:**
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
   ```

2. **Run the bot:**
   ```bash
   python main.py
   ```

3. **Find your bot on Telegram:**
   - Search for the username you created (e.g., @my_dnd_bot)
   - Click **Start** or type `/start`

4. **The bot is now connected!** 🎉

---

## ⚠️ Important Notes

## 🤝 Credits

- **DM AI** - Powered by DeepSeek
- **Database** - Supabase
- **Bot Framework** - python-telegram-bot
- **Adventure Module** - Lost Mine of Phandelver (DnD 5e)

---

## 📜 License

MIT License - Feel free to use and modify!

---

## ❓ Troubleshooting

**Bot not responding?**
- Check `.env` credentials are correct
- Verify Supabase tables exist
- Check DeepSeek API has credits

**Database errors?**
- Run `schema.sql` in Supabase SQL Editor
- Verify URL and keys are correct

**Deployment issues?**
- Check Python and dependencies are installed correctly

---

*Created for Cantonese-speaking DnD players!* 🎲🧝
