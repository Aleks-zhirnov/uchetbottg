import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
CHOOSING_ACTION, ADDING_CLIENT, ADDING_ITEMS, ADDING_ITEM_NAME, ADDING_ITEM_QTY, ADDING_ITEM_PRICE, ADDING_ITEM_DISCOUNT, SETTING_GENERAL_DISCOUNT = range(8)

# Хранилище данных (в production используйте базу данных)
orders_db = {}
current_order = {}

# Токен бота - ЗАМЕНИТЕ НА ВАШ!
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8465090691:AAGx-sekLkXf4Si14wVeU09rWWrXiOS92KI')

def number_to_words_ru(num):
    """Конвертирует число в слова на русском"""
    ones = ['', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
    tens = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']
    hundreds = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот']
    teens = ['десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать', 'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
    thousands = ['тысяча', 'тысячи', 'тысяч']
    
    if num == 0:
        return 'ноль рублей'
    
    int_part = int(num)
    result = ''
    
    if int_part >= 1000:
        th = int_part // 1000
        th_mod = th % 10
        th_mod100 = th % 100
        
        if th >= 100:
            result += hundreds[th // 100] + ' '
        if th_mod100 >= 10 and th_mod100 < 20:
            result += teens[th_mod100 - 10] + ' '
        else:
            if (th % 100) // 10 > 0:
                result += tens[(th % 100) // 10] + ' '
            if th_mod > 0:
                fem_ones = ['', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
                result += fem_ones[th_mod] + ' '
        
        if th_mod100 >= 10 and th_mod100 <= 20:
            result += thousands[2] + ' '
        elif th_mod == 1:
            result += thousands[0] + ' '
        elif th_mod >= 2 and th_mod <= 4:
            result += thousands[1] + ' '
        else:
            result += thousands[2] + ' '
    
    remainder = int_part % 1000
    if remainder >= 100:
        result += hundreds[remainder // 100] + ' '
    last_two = remainder % 100
    if last_two >= 10 and last_two < 20:
        result += teens[last_two - 10] + ' '
    else:
        if last_two // 10 > 0:
            result += tens[last_two // 10] + ' '
        if last_two % 10 > 0:
            result += ones[last_two % 10] + ' '
    
    last_digit = int_part % 10
    last_two_digits = int_part % 100
    
    if last_two_digits >= 11 and last_two_digits <= 14:
        result += 'рублей'
    elif last_digit == 1:
        result += 'рубль'
    elif last_digit >= 2 and last_digit <= 4:
        result += 'рубля'
    else:
        result += 'рублей'
    
    kopeks = round((num - int_part) * 100)
    result += f' {kopeks:02d} копеек'
    
    return result.strip().capitalize()

def calculate_order_total(order):
    """Вычисляет итоговую стоимость заказа"""
    subtotal = 0
    for item in order.get('items', []):
        item_total = item['quantity'] * item['price']
        item_total -= item_total * item.get('discount', 0) / 100
        subtotal += item_total
    
    general_discount = order.get('general_discount', 0)
    total = subtotal - (subtotal * general_discount / 100)
    return subtotal, total

def generate_pdf(order):
    """Генерирует PDF документ заказа"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Заголовок
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, alignment=1)
    date_str = datetime.fromisoformat(order['date']).strftime('%d.%m.%Y')
    title = Paragraph(f"<b>Заказ от {date_str}</b>", title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Информация о заказе
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=11)
    elements.append(Paragraph(f"<b>Исполнитель:</b> {order['executor']}", info_style))
    elements.append(Paragraph(f"<b>Заказчик:</b> {order['client_name']}, тел.: {order['client_phone']}", info_style))
    elements.append(Spacer(1, 12))
    
    # Таблица товаров
    has_discounts = order.get('general_discount', 0) > 0 or any(item.get('discount', 0) > 0 for item in order['items'])
    
    if has_discounts:
        table_data = [['№', 'Товары (работы, услуги)', 'Кол-во', 'Цена', 'Скидка', 'Сумма']]
    else:
        table_data = [['№', 'Товары (работы, услуги)', 'Кол-во', 'Цена', 'Сумма']]
    
    for idx, item in enumerate(order['items'], 1):
        item_subtotal = item['quantity'] * item['price']
        item_total = item_subtotal - (item_subtotal * item.get('discount', 0) / 100)
        
        if has_discounts:
            discount_str = f"{item.get('discount', 0)}%" if item.get('discount', 0) > 0 else '-'
            table_data.append([
                str(idx),
                item['name'],
                str(item['quantity']),
                f"{item['price']:.2f}",
                discount_str,
                f"{item_total:.2f}"
            ])
        else:
            table_data.append([
                str(idx),
                item['name'],
                str(item['quantity']),
                f"{item['price']:.2f}",
                f"{item_total:.2f}"
            ])
    
    if has_discounts:
        col_widths = [30, 200, 50, 70, 60, 70]
    else:
        col_widths = [30, 250, 50, 70, 70]
    
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    
    # Итоговая сумма
    subtotal, total = calculate_order_total(order)
    
    if order.get('general_discount', 0) > 0:
        elements.append(Paragraph(f"<b>Сумма:</b> {subtotal:.2f} руб.", info_style))
        elements.append(Paragraph(f"<b>Общая скидка:</b> {order['general_discount']}%", info_style))
        elements.append(Paragraph(f"<b>Итого со скидкой:</b> {total:.2f} руб.", info_style))
    else:
        elements.append(Paragraph(f"<b>Итого:</b> {total:.2f} руб.", info_style))
    
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Всего наименований {len(order['items'])}, на сумму {total:.2f} руб.", info_style))
    elements.append(Paragraph(f"<b>{number_to_words_ru(total)}</b>", info_style))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("📝 Создать заказ", callback_data='new_order')],
        [InlineKeyboardButton("📋 Мои заказы", callback_data='list_orders')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Добро пожаловать в систему управления заказами!\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )
    return CHOOSING_ACTION

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == 'new_order':
        current_order[user_id] = {
            'date': datetime.now().isoformat(),
            'executor': 'Жирнов А.С.',
            'items': [],
            'general_discount': 0
        }
        await query.edit_message_text(
            "📝 Создание нового заказа\n\n"
            "Введите имя заказчика:"
        )
        return ADDING_CLIENT
    
    elif query.data == 'list_orders':
        user_orders = orders_db.get(user_id, {})
        if not user_orders:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📋 У вас пока нет заказов.\n"
                "Создайте первый заказ!",
                reply_markup=reply_markup
            )
            return CHOOSING_ACTION
        
        message = "📋 *Ваши заказы:*\n\n"
        keyboard = []
        for order_id, order in user_orders.items():
            date_str = datetime.fromisoformat(order['date']).strftime('%d.%m.%Y')
            _, total = calculate_order_total(order)
            message += f"• {order['client_name']} - {date_str} - {total:.2f}₽\n"
            keyboard.append([InlineKeyboardButton(
                f"📄 {order['client_name']} ({date_str})",
                callback_data=f'view_order_{order_id}'
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return CHOOSING_ACTION
    
    elif query.data.startswith('view_order_'):
        order_id = query.data.replace('view_order_', '')
        order = orders_db[user_id][order_id]
        
        # Генерируем PDF
        pdf_buffer = generate_pdf(order)
        
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_buffer,
            filename=f"Заказ_{order['client_name']}.pdf",
            caption=f"📄 Заказ для {order['client_name']}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 К списку заказов", callback_data='list_orders')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Документ готов!", reply_markup=reply_markup)
        return CHOOSING_ACTION
    
    elif query.data == 'help':
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "ℹ️ *Помощь*\n\n"
            "Этот бот помогает создавать и управлять заказами.\n\n"
            "*Команды:*\n"
            "/start - Главное меню\n"
            "/cancel - Отменить текущее действие\n\n"
            "*Как создать заказ:*\n"
            "1. Нажмите 'Создать заказ'\n"
            "2. Введите данные заказчика\n"
            "3. Добавьте товары/услуги\n"
            "4. Укажите цены и скидки\n"
            "5. Получите PDF документ",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return CHOOSING_ACTION
    
    elif query.data == 'back_to_menu':
        keyboard = [
            [InlineKeyboardButton("📝 Создать заказ", callback_data='new_order')],
            [InlineKeyboardButton("📋 Мои заказы", callback_data='list_orders')],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)
        return CHOOSING_ACTION
    
    elif query.data == 'add_item':
        await query.edit_message_text("Введите название товара/услуги:")
        return ADDING_ITEM_NAME
    
    elif query.data == 'finish_order':
        user_id = query.from_user.id
        order = current_order[user_id]
        
        # Сохраняем заказ
        if user_id not in orders_db:
            orders_db[user_id] = {}
        
        order_id = f"order_{len(orders_db[user_id]) + 1}"
        orders_db[user_id][order_id] = order
        
        # Генерируем PDF
        pdf_buffer = generate_pdf(order)
        
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=pdf_buffer,
            filename=f"Заказ_{order['client_name']}.pdf",
            caption=f"✅ Заказ успешно создан!\n\n📄 Документ для {order['client_name']}"
        )
        
        del current_order[user_id]
        
        keyboard = [[InlineKeyboardButton("🔙 В главное меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Готово! Что дальше?", reply_markup=reply_markup)
        return CHOOSING_ACTION
    
    elif query.data == 'set_general_discount':
        await query.edit_message_text(
            "Введите общую скидку на весь заказ (в процентах):\n"
            "Например: 10\n\n"
            "Или отправьте 0 если скидки нет."
        )
        return SETTING_GENERAL_DISCOUNT

async def receive_client_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение информации о заказчике"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    if 'client_name' not in current_order[user_id]:
        current_order[user_id]['client_name'] = text
        await update.message.reply_text(
            f"✅ Заказчик: {text}\n\n"
            "Теперь введите номер телефона:"
        )
        return ADDING_CLIENT
    else:
        current_order[user_id]['client_phone'] = text
        keyboard = [[InlineKeyboardButton("➕ Добавить товар/услугу", callback_data='add_item')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ Заказчик: {current_order[user_id]['client_name']}\n"
            f"✅ Телефон: {text}\n\n"
            "Теперь добавьте товары или услуги:",
            reply_markup=reply_markup
        )
        return ADDING_ITEMS

async def receive_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия товара"""
    user_id = update.message.from_user.id
    context.user_data['temp_item'] = {'name': update.message.text}
    
    await update.message.reply_text(
        f"✅ Название: {update.message.text}\n\n"
        "Введите количество:"
    )
    return ADDING_ITEM_QTY

async def receive_item_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение количества товара"""
    try:
        quantity = float(update.message.text)
        if quantity <= 0:
            raise ValueError
        
        context.user_data['temp_item']['quantity'] = quantity
        await update.message.reply_text(
            f"✅ Количество: {quantity}\n\n"
            "Введите цену за единицу (в рублях):"
        )
        return ADDING_ITEM_PRICE
    except:
        await update.message.reply_text(
            "❌ Неверный формат. Введите число больше 0:"
        )
        return ADDING_ITEM_QTY

async def receive_item_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение цены товара"""
    try:
        price = float(update.message.text)
        if price < 0:
            raise ValueError
        
        context.user_data['temp_item']['price'] = price
        await update.message.reply_text(
            f"✅ Цена: {price}₽\n\n"
            "Введите скидку на этот товар (в процентах):\n"
            "Например: 10\n\n"
            "Или отправьте 0 если скидки нет."
        )
        return ADDING_ITEM_DISCOUNT
    except:
        await update.message.reply_text(
            "❌ Неверный формат. Введите цену (число):"
        )
        return ADDING_ITEM_PRICE

async def receive_item_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение скидки на товар"""
    try:
        discount = float(update.message.text)
        if discount < 0 or discount > 100:
            raise ValueError
        
        user_id = update.message.from_user.id
        context.user_data['temp_item']['discount'] = discount
        
        # Добавляем товар в заказ
        current_order[user_id]['items'].append(context.user_data['temp_item'])
        
        # Показываем информацию о текущем заказе
        order = current_order[user_id]
        message = f"✅ Товар добавлен!\n\n📋 *Текущий заказ:*\n"
        message += f"Заказчик: {order['client_name']}\n"
        message += f"Телефон: {order['client_phone']}\n\n"
        message += "*Товары:*\n"
        
        for idx, item in enumerate(order['items'], 1):
            item_total = item['quantity'] * item['price']
            if item['discount'] > 0:
                item_total -= item_total * item['discount'] / 100
                message += f"{idx}. {item['name']} - {item['quantity']}шт × {item['price']}₽ (скидка {item['discount']}%) = {item_total:.2f}₽\n"
            else:
                message += f"{idx}. {item['name']} - {item['quantity']}шт × {item['price']}₽ = {item_total:.2f}₽\n"
        
        _, total = calculate_order_total(order)
        message += f"\n*Итого: {total:.2f}₽*"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить еще товар", callback_data='add_item')],
            [InlineKeyboardButton("💰 Установить общую скидку", callback_data='set_general_discount')],
            [InlineKeyboardButton("✅ Завершить заказ", callback_data='finish_order')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return ADDING_ITEMS
    except:
        await update.message.reply_text(
            "❌ Неверный формат. Введите скидку от 0 до 100:"
        )
        return ADDING_ITEM_DISCOUNT

async def receive_general_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение общей скидки на заказ"""
    try:
        discount = float(update.message.text)
        if discount < 0 or discount > 100:
            raise ValueError
        
        user_id = update.message.from_user.id
        current_order[user_id]['general_discount'] = discount
        
        order = current_order[user_id]
        subtotal, total = calculate_order_total(order)
        
        message = f"✅ Общая скидка {discount}% установлена!\n\n"
        message += f"Сумма без скидки: {subtotal:.2f}₽\n"
        message += f"*Итого со скидкой: {total:.2f}₽*"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить еще товар", callback_data='add_item')],
            [InlineKeyboardButton("✅ Завершить заказ", callback_data='finish_order')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return ADDING_ITEMS
    except:
        await update.message.reply_text(
            "❌ Неверный формат. Введите скидку от 0 до 100:"
        )
        return SETTING_GENERAL_DISCOUNT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена текущего действия"""
    user_id = update.message.from_user.id
    if user_id in current_order:
        del current_order[user_id]
    
    keyboard = [
        [InlineKeyboardButton("📝 Создать заказ", callback_data='new_order')],
        [InlineKeyboardButton("📋 Мои заказы", callback_data='list_orders')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❌ Действие отменено.\n\nВыберите действие:",
        reply_markup=reply_markup
    )
    return CHOOSING_ACTION

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Создаем ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING_ACTION: [CallbackQueryHandler(button_callback)],
            ADDING_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_client_info)],
            ADDING_ITEMS: [CallbackQueryHandler(button_callback)],
            ADDING_ITEM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_item_name)],
            ADDING_ITEM_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_item_quantity)],
            ADDING_ITEM_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_item_price)],
            ADDING_ITEM_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_item_discount)],
            SETTING_GENERAL_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_general_discount)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    
    # Запускаем бота
    print("🤖 Бот запущен!")
    application.run_polling()

if __name__ == '__main__':
    main()
