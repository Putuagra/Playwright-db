from telegram.ext import CommandHandler, ContextTypes, Application, CallbackQueryHandler, MessageHandler, filters
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from playwright.async_api import async_playwright
import logging
import os
import io
from dotenv import load_dotenv
import asyncpg

load_dotenv()

token = os.getenv("TELEGRAM_TOKEN")
database = os.getenv("DATABASE")
host = os.getenv("HOST")
user = os.getenv("USER")
password = os.getenv("PASSWORD")
port = os.getenv("PORT")

async def getConnection():
    return await asyncpg.connect(
        database=database,
        user=user,
        password=password,
        host=host,
        port=port,
    )

async def fetchDashboards():
    conn = await getConnection()
    try:
        query = "SELECT * FROM dashboards ORDER BY id_dashboard ASC;"
        data = await conn.fetch(query)
        dashboards = {}
        if data:
            for idx, data_db in enumerate(data, start=1):
                id_dashboard, url, title = data_db['id_dashboard'], data_db['url'], data_db['title']
                dashboard_data = {
                    "id": id_dashboard,
                    "title": title,
                    "url": url
                }
                dashboards[idx] = dashboard_data
        else:
            print("Failed to fetch dashboards from database.")
        return dashboards
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        await conn.close()

# Log
log_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../Log'))
log_path = os.path.join(log_folder, 'dashboard.log')

os.makedirs(log_folder, exist_ok=True)

logging.basicConfig(
    filename=log_path,
    filemode='a',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Menu Inline /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    id = update.message.from_user.id
    name = update.message.from_user.full_name
    logger.info(f"{id} - {name} accessing bot")
    keyboard = [[InlineKeyboardButton("Menu", callback_data="menu")]]
    keyboard.append([InlineKeyboardButton("Add New Dashboard", callback_data="add_new")])
    keyboard.append([InlineKeyboardButton("Update Dashboard", callback_data="update")])
    keyboard.append([InlineKeyboardButton("Remove Dashboard", callback_data="delete")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text('Choose an option:', reply_markup=reply_markup)

# Callback1 - callback8 to run menu
async def captureCallback(update: Update, context: ContextTypes.DEFAULT_TYPE, obj):
    logger.info(f"Capture callback for {obj["title"]}")
    query = update.callback_query
    await query.message.reply_text(f"{obj["title"]} - Please wait in few seconds")
 
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try: 
            await page.goto(obj["url"], timeout=120000)
            await page.wait_for_selector('.grid-viewport', timeout=120000, state='visible')
            dimensions = await page.evaluate('''() => {
                return {
                    width: document.querySelector('.grid-viewport').scrollWidth,
                    height: document.querySelector('.grid-viewport').scrollHeight
                };
            }''')

            await page.set_viewport_size({"width": dimensions["width"], "height": dimensions["height"]+160})
            
            await page.wait_for_timeout(15000)
            
            image = await page.screenshot(type="png", timeout=120000)
            img_io = io.BytesIO(image)
            filename_with_extension = f"{obj['title']}.png"  
            await context.bot.send_document(chat_id=query.message.chat_id, document=img_io, filename=filename_with_extension)
            logger.info("Screenshot taken and sent")
        except Exception as e:
            error_message = f"An unexpected error occurred: {str(e)}"
            await query.message.reply_text(error_message)
            print(error_message)
            logger.error(error_message)
        finally:
            await browser.close()
            await start(query, context)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    message = update.message
    dashboards = await fetchDashboards()
    state = context.user_data.get('state')
    action = context.user_data.get('action')
    data_update = context.user_data.get('object')

    if query:
        data = query.data
        await query.answer()
        
        if data in ["delete", "update", "menu", "add_new"]:
            if not dashboards and data != "add_new":
                await query.message.reply_text("No dashboards found.")
                return
            
            action = "Remove" if data == "delete" else ("Update" if data == "update" else "Maverick MMP - L1 Dashboard")
            state = "stateDelete" if data == "delete" else ("stateUpdate" if data == "update" else ("stateNew" if data == "add_new" else None))
            
            if data != "add_new":
                button_data = [(f"{key}. {dashboards[key]['title']}", str(key)) for key in dashboards]
                menuKeyboard = [[InlineKeyboardButton(label, callback_data=data)] for label, data in button_data]
                if data == "menu":
                    menuKeyboard.append([InlineKeyboardButton("Back", callback_data="back")])
        
                reply_markup = InlineKeyboardMarkup(menuKeyboard)
                await query.edit_message_text(f'Choose an option for {action}:', reply_markup=reply_markup)
            else:
                await query.message.reply_text(text="Please enter title for dashboard:")
            
            context.user_data['state'] = state
            context.user_data['action'] = "insert" if data == "add_new" else ("update" if data == "update" else None)
        
        elif state in ["stateDelete", "stateUpdate"]:
            dashboard_mapping = {str(key): dashboards[key] for key in dashboards}
            obj = dashboard_mapping.get(data)
            if obj:
                if state == "stateUpdate":
                    context.user_data['object'] = obj
                    await query.message.reply_text(text="Please enter title for dashboard:")
                    context.user_data['state'] = "stateUpdateTitle"
                else:
                    await deleteDashboard(obj['id'])
                    await query.message.reply_text("Dashboard has been removed.")
                    context.user_data['state'] = None
                    await start(query, context)
        elif data == "back":
            await start(query, context)
        else:
            dashboard_mapping = {str(key): dashboards[key] for key in dashboards}
            obj = dashboard_mapping.get(data)

            if obj:
                asyncio.create_task(captureCallback(update, context, obj))

    elif message:
        if state in ["stateNew", "stateUpdateTitle"]:
            context.user_data['title'] = message.text
            await message.reply_text('Please enter the URL for dashboard:')
            context.user_data['state'] = "stateUrl"
        elif state == "stateUrl":
            title = context.user_data['title']
            url = message.text
            if action == "insert":
                await insertDashboard(title, url)
                await message.reply_text(f"Dashboard '{title}' has been added.")
            elif action == 'update':
                await updateDashboard(title, url, data_update['id'])
                await message.reply_text("Dashboard has been updated.")
            context.user_data['state'] = None
            context.user_data['action'] = None
            context.user_data['data_update'] = None
            await start(update, context)

async def insertDashboard(title, url):
    conn = await getConnection()
    try:
        await conn.execute("INSERT INTO dashboards (title, url) VALUES ($1, $2)", title, url)
        logger.info(f"Inserted new dashboard: {title}, {url}")
    except Exception as e:
        logger.error(f"Error inserting dashboard: {e}")
    finally:
        await conn.close()
        
async def updateDashboard(title, url, id):
    conn = await getConnection()
    try:
        await conn.execute("UPDATE dashboards SET title = $1, url = $2 WHERE id_dashboard = $3;", title, url, id)
        logger.info(f"Updated dashboard: {title}, {url}")
    except Exception as e:
        logger.error(f"Error updating dashboard: {e}")
    finally:
        await conn.close()
        
async def deleteDashboard(id):
    conn = await getConnection()
    try:
        await conn.execute("DELETE FROM dashboards WHERE id_dashboard = $1", id)
        logger.info(f"Removed dashboard")
    except Exception as e:
        logger.error(f"Error removing dashboard: {e}")
    finally:
        await conn.close()
    
async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asyncio.create_task(start(update, context))
    
def main():
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_task))
    
    application.add_handler(CallbackQueryHandler(button))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()