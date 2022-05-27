import logging
import os
import pathlib
from enum import Enum, auto
from time import sleep

import requests
from environs import Env
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (CallbackContext,
                          CallbackQueryHandler,
                          CommandHandler,
                          ConversationHandler,
                          Filters,
                          MessageHandler,
                          Updater)

from helpers import download_photo, get_capture_text
from moltin_handlers import (generate_moltin_token,
                             get_product_data,
                             get_cart_items,
                             add_product_to_cart,
                             delete_product_from_cart,
                             create_customer)


logger = logging.getLogger('TGBotLogger')


class State(Enum):
    SHOW_MENU = auto()
    HANDLE_MENU = auto()
    HANDLE_DESCRIPTION = auto()
    HANDLE_CART = auto()
    WAITING_EMAIL = auto()


def get_main_menu_markup(token):
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'EP-Channel': 'web store'
    }
    response = requests.get('https://api.moltin.com/catalog/products/',
                            headers=headers)
    response.raise_for_status()
    products_in_catalog = response.json()['data']

    buttons = [[InlineKeyboardButton(product['attributes']['name'],
                                     callback_data=product['id'])]
               for product in products_in_catalog]
    buttons.append([InlineKeyboardButton('🛒 КОРЗИНА', callback_data='cart')])
    return InlineKeyboardMarkup(buttons)


def show_cart(update, context, headers):
    user_query = update.callback_query
    context.bot.delete_message(chat_id=user_query.message.chat_id,
                               message_id=user_query.message.message_id)
    cart_items = get_cart_items(headers, update.effective_user.id)
    text = ''
    buttons = []
    for item in cart_items['data']:
        text += (f'✔ {item["name"]}\n'
                 f'{item["meta"]["display_price"]["with_tax"]["unit"]["formatted"]}/кг\n'
                 f'{item["quantity"]} кг на '
                 f'{item["meta"]["display_price"]["with_tax"]["value"]["formatted"]}\n\n')
        buttons.append(
            [InlineKeyboardButton(f'{item["name"]} ✖️',
                                  callback_data=item['id'])]
        )

    text += f'ИТОГО: {cart_items["meta"]["display_price"]["with_tax"]["formatted"]}'
    buttons.append([InlineKeyboardButton('📄 В МЕНЮ', callback_data='get_menu')])
    buttons.append([InlineKeyboardButton('💳 ОПЛАТА', callback_data='check_out')])
    context.bot.send_message(chat_id=update.effective_user.id,
                             text=text,
                             reply_markup=InlineKeyboardMarkup(buttons))


def start(update: Update, context: CallbackContext):
    user = update.effective_user
    update.message.reply_markdown_v2(
        text=f'Привет, {user.mention_markdown_v2()}\! Хотите заказать рыбки?',
        reply_markup=get_main_menu_markup(context.bot_data['moltin_token'])
    )
    return State.HANDLE_MENU


def show_menu(update: Update, context: CallbackContext):
    user_query = update.callback_query
    context.bot.delete_message(chat_id=user_query.message.chat_id,
                               message_id=user_query.message.message_id)
    context.bot.send_message(
        chat_id=user_query.message.chat_id,
        text='Пожалуйста, выберите товар:',
        reply_markup=get_main_menu_markup(context.bot_data['moltin_token'])
    )
    return State.HANDLE_MENU


def handle_menu(update: Update, context: CallbackContext):
    user_query = update.callback_query
    moltin_headers = context.bot_data['moltin_headers']

    if user_query['data'] == 'cart':
        show_cart(update, context, moltin_headers)
        return State.HANDLE_CART

    context.user_data['product_id'] = user_query.data
    context.bot.delete_message(chat_id=user_query.message.chat_id,
                               message_id=user_query.message.message_id)

    product_data = get_product_data(moltin_headers, user_query)
    product_img_id = product_data['relationships']['main_image']['data']['id']
    download_photo(moltin_headers, product_img_id)

    for filename in os.listdir('images'):
        if filename.startswith(product_img_id):
            with open(f'images/{filename}', 'rb') as image:
                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton('➕ 1 кг', callback_data=1),
                            InlineKeyboardButton('➕ 5 кг', callback_data=5),
                            InlineKeyboardButton('➕ 10 кг', callback_data=10)
                        ],
                        [InlineKeyboardButton('🛒 КОРЗИНА', callback_data='cart')],
                        [InlineKeyboardButton('Назад', callback_data='back')]
                    ]
                )

                context.bot.send_photo(chat_id=user_query.message.chat_id,
                                       photo=image,
                                       caption=get_capture_text(product_data),
                                       reply_markup=reply_markup)
                return State.HANDLE_DESCRIPTION


def handle_description(update: Update, context: CallbackContext):
    user_query = update.callback_query
    moltin_headers = context.bot_data['moltin_headers']

    if user_query['data'] == 'back':
        return State.SHOW_MENU
    if user_query['data'] == 'cart':
        show_cart(update, context, moltin_headers)
        return State.HANDLE_CART

    cart_response = add_product_to_cart(headers=moltin_headers,
                                        cart_id=update.effective_user.id,
                                        product_id=context.user_data['product_id'],
                                        qty=user_query['data'])
    if 'errors' in cart_response:
        if cart_response['errors'][0]['title'] == 'Insufficient stock':
            context.bot.send_message(chat_id=user_query.message.chat_id,
                                     text='Недостаточно товара в наличии')
        else:
            context.bot.send_message(chat_id=user_query.message.chat_id,
                                     text='Произошла ошибка. Попробуйте снова')
        return State.HANDLE_DESCRIPTION
    context.bot.send_message(chat_id=user_query.message.chat_id,
                             text=f'Добавили в корзину {user_query["data"]} кг')


def handle_cart(update: Update, context: CallbackContext):
    user_query = update.callback_query

    if user_query['data'] == 'get_menu':
        return State.SHOW_MENU

    elif user_query['data'] == 'check_out':
        context.bot.send_message(chat_id=user_query.message.chat_id,
                                 text='Пришлите, пожалуйста, ваш email')
        return State.WAITING_EMAIL

    delete_product_from_cart(headers=context.bot_data['moltin_headers'],
                             cart_id=update.effective_user.id,
                             product_id=user_query['data'])
    show_cart(update, context, context.bot_data['moltin_headers'])
    return State.HANDLE_CART


def handle_user_details(update: Update, context: CallbackContext):
    users_email = update.message.text
    update.message.reply_text(
        f'Благодарим за заказ! Мы свяжемся с вами по email {users_email}'
    )
    create_customer(headers=context.bot_data['moltin_headers'],
                    customer_id=update.effective_user.id,
                    name=update.effective_user.first_name,
                    email=users_email)


def finish(update: Update, context: CallbackContext):
    update.message.reply_text('Будем рады видеть вас снова 😊')
    return ConversationHandler.END


def main():
    env = Env()
    env.read_env()

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO)

    tg_bot_token = env.str('TG_BOT_TOKEN')
    moltin_client_id = env.str('MOLTIN_CLIENT_ID')
    moltin_secret_key = env.str('MOLTIN_SECRET_KEY')

    pathlib.Path('images/').mkdir(exist_ok=True)

    updater = Updater(tg_bot_token)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            State.SHOW_MENU: [
                CallbackQueryHandler(show_menu),
            ],
            State.HANDLE_MENU: [
                CommandHandler('start', start),
                CallbackQueryHandler(handle_menu),
            ],
            State.HANDLE_DESCRIPTION: [
                CallbackQueryHandler(handle_description),
            ],
            State.HANDLE_CART: [
                CallbackQueryHandler(handle_cart),
            ],
            State.WAITING_EMAIL: [
                MessageHandler(
                    Filters.regex(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
                    handle_user_details
                )
            ]
        },
        fallbacks=[CommandHandler('finish', finish)]
    )
    moltin_token = generate_moltin_token(moltin_client_id, moltin_secret_key)
    moltin_headers = {
        'Authorization': f'Bearer {moltin_token}',
        'Content-Type': 'application/json',
    }

    dispatcher.bot_data['moltin_token'] = moltin_token
    dispatcher.bot_data['moltin_headers'] = moltin_headers
    dispatcher.add_handler(conv_handler)

    while True:
        try:
            updater.start_polling()
            updater.idle()
        except Exception as err:
            logger.exception(f"⚠ Ошибка бота:\n\n {err}")
            sleep(60)


if __name__ == '__main__':
    main()
