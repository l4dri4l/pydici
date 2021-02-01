#!/usr/bin/env python
# coding: utf-8

"""
Telegram bot for timesheet input and various reminder

@author: Sébastien Renard (sebastien.renard@digitalfox.org)
@license: AGPL v3 or newer (http://www.gnu.org/licenses/agpl-3.0.html)
"""

import sys
import os
from os.path import abspath, join, dirname, pardir
import logging
from datetime import date, datetime
import random

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO,
)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, ConversationHandler, JobQueue

# # Setup django envt & django imports
PYDICI_DIR = abspath(join(dirname(__file__), pardir))
os.environ['DJANGO_SETTINGS_MODULE'] = "pydici.settings"

sys.path.append(PYDICI_DIR)  # Add project path to python path

# Ensure we are in the good current working directory (pydici home)
os.chdir(PYDICI_DIR)

# Django imports
from django.core.wsgi import get_wsgi_application
from django.db.models import Sum
from django.db import transaction
from django.utils.translation import ugettext as _


# Init and model loading
application = get_wsgi_application()

# Pydici imports
from people.models import Consultant
from people.utils import compute_consultant_tasks
from staffing.models import Mission, Timesheet, Holiday
from core.utils import get_parameter

logger = logging.getLogger(__name__)

# Stages
MISSION_SELECT, MISSION_TIMESHEET = range(2)

NONPROD_BUTTON = InlineKeyboardButton("non production mission ?", callback_data="NONPROD")


def check_user_is_declared(update, context):
    """Ensure user we are talking is defined in our database. If yes, return consultant object"""
    try:
        user = update.message.from_user
        consultant = Consultant.objects.get(telegram_alias="%s" % user.name.lstrip("@"), active=True)
        return consultant
    except Consultant.DoesNotExist:
        update.message.reply_text(_("sorry, i don't know you"))
        return ConversationHandler.END


def mission_keyboard(consultant, nature):
    keyboard = []
    for mission in consultant.forecasted_missions():
        if mission.nature == nature:
            keyboard.append([InlineKeyboardButton(mission.short_name(), callback_data=str(mission.id)),])
    return keyboard


def remaining_time_to_declare(context):
    consultant = context.user_data["consultant"]
    today = date.today()
    holidays = Holiday.objects.all()
    if today.weekday() < 15 and today not in holidays:
        declared = Timesheet.objects.filter(consultant=consultant, working_date=today).aggregate(Sum("charge"))[
                       "charge__sum"] or 0
        return 1 - declared - sum(context.user_data["timesheet"].values())
    else:
        return 0


def start(update, context):
    """Start timesheet session when user type /start"""
    user = update.message.from_user
    if update.effective_chat.id < 0:
        update.message.reply_text(_("I am too shy to do that in public. Let's go private :-)"))
        return ConversationHandler.END

    consultant = check_user_is_declared(update, context)

    if consultant == ConversationHandler.END:
        return ConversationHandler.END

    context.user_data["consultant"] = consultant
    context.user_data["mission_nature"] = "PROD"
    context.user_data["timesheet"] = {}

    keyboard = mission_keyboard(consultant, "PROD")
    keyboard.append([NONPROD_BUTTON])

    update.message.reply_text(_("On what did you work today ?"), reply_markup=InlineKeyboardMarkup(keyboard))

    return MISSION_SELECT


def mission_timesheet(update, context):
    """Declare timesheet for given mission"""
    query = update.callback_query
    query.answer()
    mission = Mission.objects.get(id=int(query.data))
    context.user_data["mission"] = mission
    keyboard = [
        [
            InlineKeyboardButton("0", callback_data="0"),
            InlineKeyboardButton("0.25", callback_data="0.25"),
            InlineKeyboardButton("0.5", callback_data="0.5"),
            InlineKeyboardButton("0.75", callback_data="0.75"),
            InlineKeyboardButton("1", callback_data="1"),
        ]
    ]
    query.edit_message_text(
        text="how much did you work on %(mission)s ? (%(time)s is remaining for today)" % (mission.short_name(), remaining_time_to_declare(context)),
        reply_markup=InlineKeyboardMarkup(keyboard))
    return MISSION_TIMESHEET


def end(update, context):
    """Returns `ConversationHandler.END`, which tells the  ConversationHandler that the conversation is over"""
    query = update.callback_query
    query.answer()
    consultant = context.user_data["consultant"]
    with transaction.atomic():
        try:
            Timesheet.objects.filter(consultant=consultant, working_date=date.today()).delete()
            for mission, charge in context.user_data["timesheet"].items():
                Timesheet.objects.create(mission=mission, consultant=consultant,
                                         charge=charge, working_date=date.today())
        except:
            query.edit_message_text(text=_("Oups, cannot update your timesheet, sorry"))
            return ConversationHandler.END
    msg = _("You timesheet was updated:\n")
    msg += "\n - ".join(["%s : %s" % (m.short_name(), c) for m, c in context.user_data["timesheet"].items()])
    total = sum(context.user_data["timesheet"].values())
    if total > 1:
        msg += _("\n\nWhat a day, %s declared. Time to get some rest!") % total
    elif total < 1:
        msg += _("\n\nOnly %s today. Don't you forget to declare something ?") % total
    query.edit_message_text(text=msg)
    return ConversationHandler.END


def select_mission(update, context):
    """Select mission to update"""
    query = update.callback_query
    query.answer()
    consultant = context.user_data["consultant"]
    if query.data == "NONPROD":
        context.user_data["mission_nature"] = "NONPROD"
    else:
        mission = context.user_data["mission"]
        context.user_data["timesheet"][mission] = float(query.data)

    keyboard = mission_keyboard(consultant, context.user_data["mission_nature"])

    if context.user_data["mission_nature"] == "PROD":
        keyboard.append([NONPROD_BUTTON])
    else:
        keyboard.append([InlineKeyboardButton(_("That's all for today !"), callback_data="END")])

    query.edit_message_text(text=_("On which other mission did you work today ?"), reply_markup=InlineKeyboardMarkup(keyboard))
    return MISSION_SELECT


def alert_consultant(context):
    """Randomly alert consultant about important stuff to do"""
    now = datetime.now()
    #if now.weekday() in (5,6) or now.hour < 9 or now.hour > 19:
        # don't bother people outside business hours
    #    return
    consultants = Consultant.objects.exclude(telegram_id=None).filter(active=True)
    if not consultants:
        logger.warning("No consultant have telegram id defined. Alerting won't be possible. Bye")
        return
    consultant = random.choice(consultants)
    #TODO: add pressure control mechanism to avoid persecuting people we already warned x times before
    tasks = compute_consultant_tasks(consultant)
    if tasks:
        task_name, task_count, task_link, task_priority = random.choice(tasks)
        url = get_parameter("HOST") + task_link
        msg = _("Hey, what about thinking about that: %(task_name)s (x%(task_count)s)\n%(link)s") % {"task_name": task_name,
                                                                                                     "task_count": task_count,
                                                                                                     "link": url}
        context.bot.send_message(chat_id=consultant.telegram_id, text=msg)


def hello(update, context):
    user = update.message.from_user

    consultant = check_user_is_declared(update, context)

    if consultant == ConversationHandler.END:
        return ConversationHandler.END

    if consultant.telegram_id:
        update.message.reply_text(_("very happy to see you again !"))
    else:
        consultant.telegram_id = user.id
        consultant.save()
        update.message.reply_text(_("I very pleased to meet you !"))

    return ConversationHandler.END


def main():
    #TODO: get if from config file as well
    updater = Updater(os.environ["PYDICI_TOKEN"], use_context=True)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start),
                      CommandHandler("hello", hello)],
        states={
            MISSION_SELECT: [
                CallbackQueryHandler(select_mission, pattern="NONPROD"),
                CallbackQueryHandler(end, pattern="END"),
                CallbackQueryHandler(mission_timesheet),
            ],
            MISSION_TIMESHEET: [
                CallbackQueryHandler(select_mission),
            ],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    # Add ConversationHandler to dispatcher
    dispatcher.add_handler(conv_handler)

    # Add alert job
    updater.job_queue.run_repeating(alert_consultant, 3)

    # Start the Bot
    updater.start_polling()

    # Run the bot until Ctrl-C or SIGINT,SIGTERM or SIGABRT.
    updater.idle()


if __name__ == '__main__':
    main()
