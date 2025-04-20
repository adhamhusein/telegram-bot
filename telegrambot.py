import os
import tempfile
import logging
from datetime import datetime
from telegram import Update, InputFile
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import json
from dotenv import load_dotenv

from sys import path
server_path = '/var/www/portfolio-website/app'
local_path = os.path.abspath('../04_Git_Portfolio_Routing/app')
if os.path.exists(server_path):
    path.append(server_path)
elif os.path.exists(local_path):
    path.append(local_path)
else:
    raise FileNotFoundError("Neither server nor local app path found.")
from utils.dxf_converter import DXFConverter

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
UPLOAD_DIR = tempfile.gettempdir()

# Main document handler
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_size > 5 * 1024 * 1024:  # 5MB size limit
        await update.message.reply_text("❌ File too large. Max size is 5MB.")
        return
    caption = update.message.caption

    if not caption or not caption.startswith("/convertdxf"):
        return  # Ignore if no proper caption

    try:
        date_str = caption.split()[1]
        date = datetime.strptime(date_str, "%Y-%m-%d")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Invalid caption format. Use: /convertdxf YYYY-MM-DD")
        return

    if not doc.file_name.lower().endswith(".dxf"):
        await update.message.reply_text("❌ Please upload a valid .dxf file.")
        return

    await update.message.reply_text("⏳ Processing the file...")

    dxf_path = os.path.join(UPLOAD_DIR, doc.file_name)
    telegram_file = await context.bot.get_file(doc.file_id)
    await telegram_file.download_to_drive(dxf_path)

    try:
        # Process DXF
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        converter = DXFConverter(dxf_path)

        converter.convert_dxf(date)
        filtered_polygons = converter.filter_polygons()
        processed_points = converter.process_points(filtered_polygons, date)

        # Write SQL
        sql_filename = os.path.splitext(doc.file_name)[0] + ".sql"
        sql_path = os.path.join(UPLOAD_DIR, sql_filename)
        with open(sql_path, "w") as f:
            f.write("--=== TEXTS ===--\n")
            f.write("INSERT INTO ref_blastid (point_date, point_lon, point_lat, point_text, polygon_wkt, updated_at)\nVALUES\n")
            values = [
                f"('{date.strftime('%Y-%m-%d')}', {p['lon']}, {p['lat']}, '{p['text']}', '{p['polygon']}', GETDATE())"
                for p in processed_points
            ]
            f.write(",\n".join(values) + ";\n")

        # Write GeoJSON
        geojson_filename = os.path.splitext(doc.file_name)[0] + ".geojson"
        geojson_path = os.path.join(UPLOAD_DIR, geojson_filename)
        features = [  # Points
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p['lon'], p['lat']]},
                "properties": {"text": p['text'], "date": str(date)}
            } for p in processed_points
        ] + [  # Polygons
            {
                "type": "Feature",
                "geometry": poly.__geo_interface__,
                "properties": {}
            } for poly in filtered_polygons
        ]

        with open(geojson_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)

        # Send SQL
        with open(sql_path, "rb") as sqlf:
            await update.message.reply_document(
                document=InputFile(sqlf, filename=sql_filename),
                caption="🚀 Here's your SQL file"
            )

        # Send GeoJSON
        with open(geojson_path, "rb") as geojsonf:
            await update.message.reply_document(
                document=InputFile(geojsonf, filename=geojson_filename),
                caption="🌍 Here's your GeoJSON file"
            )

    except Exception as e:
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")

    finally:
        try:
            os.remove(dxf_path)
            os.remove(sql_path)
            os.remove(geojson_path)
        except Exception as cleanup_error:
            logging.warning(f"Cleanup failed: {cleanup_error}")

# Main setup
def main():
    logging.basicConfig(level=logging.INFO)
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.Document.ALL & filters.CaptionRegex(r"^/convertdxf "), handle_document))

    print("🚀 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
