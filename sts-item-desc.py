import os
import hmac
import hashlib
import requests
import json
from urllib.request import urlopen
from bs4 import BeautifulSoup
from flask import abort, Flask, jsonify, request
from zappa.async import task

app = Flask(__name__)

VERSION = 'v0'
SIGNING_SECRET = os.environ['SLACK_SIGNING_SECRET']
CLIENT_ID = os.environ['SLACK_CLIENT_ID']
CLIENT_SECRET = os.environ['SLACK_CLIENT_SECRET']
OAUTH_SCOPE = os.environ['SLACK_SCOPE']

search_url = 'http://slay-the-spire.fandom.com/wiki/Special:Search?query='

def is_request_valid(request):
    ts = request.headers['X-Slack-Request-Timestamp']
    body = request.get_data()
    verification_string = '{}:{}:{}'.format(VERSION, ts, body.decode('utf-8'))
    my_sig = VERSION + '=' + hmac.new(
        SIGNING_SECRET.encode('utf-8'),
        msg=verification_string.encode('utf-8'),
        digestmod=hashlib.sha256).hexdigest()
    message_sig = request.headers['X-Slack-Signature']
    return message_sig == my_sig

def urify(item):
    if item.upper() in special_names:
        return special_names[item.upper()]
    else:
        return "_".join(stupid_name_handling(w) for w in item.split())

def fix_effect_string(text):
    t = text.replace(']', '')
    t2 = t.replace('.', '. ')
    return t2.replace('  ', ' ')

def item_parser(parser, item_url):
    category_class = parser.find('div', attrs={'class': 'page-header__categories'})
    if category_class is None or "Relic" in category_class.find('a').text:
        category_text = "Relic"
        card_desc_class = parser.find('aside', attrs={'class': 'portable-infobox pi-background pi-border-color pi-theme-relic pi-layout-default'})    
    else:
        category_text = "Card"
        card_desc_class = parser.find('aside', attrs={'class': 'portable-infobox pi-background pi-border-color pi-theme-wikia pi-layout-default'})

    card_name = card_desc_class.find('h2', attrs={'class': 'pi-item pi-item-spacing pi-title pi-secondary-background'}).text.strip()

    card_image = card_desc_class.find('figure').find('a', href=True)['href']

    card_info = {'Category': category_text, 'Card Name': card_name, 'Url': item_url, 'Image': card_image}

    info = card_desc_class.findChildren('div', recursive=False)
    for i in info:
        field = i.find('h3').text
        text = i.find('div').text
        card_info[field] = fix_effect_string(text)

    return card_info

def format_card_desc(info):
    class_name = info['Class']
    if class_name == "Blue":
        class_text = "Defect"
    else:
        class_text = class_name
    type_name = info['Type']
    rarity = info['Rarity']
    if 'Cost' in info:
        cost = info['Cost']
    else:
        cost = 'X'
    effect = info['Effect']

    return ("Class: " + class_text + '\n'
        "Type: " + type_name + '\n'
        "Rarity: " + rarity + '\n'
        "Cost: " + cost + '\n'
        "Effect: " + effect)

def format_relic_desc(info):
    return ("Description: " + info['Description'] + '\n'
        "Flavor: " + info['Flavor'] + '\n'
        "Rarity: " + info['Rarity'] + '\n'
        "Class: " + info['Class'] + '\n')

def search(text):
    item_uri = "+".join(text.split())
    item_url = search_url + item_uri
    
    item_page = urlopen(item_url)
    parser = BeautifulSoup(item_page, 'html.parser')

    new_url = parser.find('a', href=True, attrs={'class': 'unified-search__result__link'})
    new_page = urlopen(new_url)
    new_parser = BeautifulSoup(new_page, 'html.parser')
  
    return (item_parser(new_parser, new_url), new_url)

@task
def search_task(request):
    text = request.form['text']

    (card_info, url) = search(text)
    category_text = card_info['Category']
    if "Card" in category_text:
        s = format_card_desc(card_info)
    else:
        s = format_relic_desc(card_info)


    button_payload = {
        'attachments': [{
            'fallback': 'sts card desc',
            'callback_id': 'sts_button',
            'title': card_info["Card Name"], 
            'title_link': card_info['Url'],
            'image_url': card_info['Image'],
            'color': 'good',
            'text': s
        }]
    }

    data = {
        'response_type': 'ephemeral',
        'replace_original': 'true',
        'attachments': [{
            'fallback': 'sts card desc',
            'callback_id': 'sts_button',
            'title': card_info["Card Name"], 
            'title_link': card_info['Url'],
            'image_url': card_info['Image'],
            'color': 'good',
            'text': s,
            'actions': [
                {
                    'name': 'send',
                    'text': 'Send to Channel',
                    'type': 'button',
                    'value': json.dumps(button_payload),
                    'style': 'primary'
                },
                {
                    'name': 'delete',
                    'text': 'Cancel',
                    'type': 'button',
                    'value': 'cancel'
                }
            ]
        }]
    }

    requests.post(request.form['response_url'], json=data) 
    

@app.route('/sts_search', methods=['POST'])
def sts_search():
    if not is_request_valid(request):
        abort(400)

    search_task(request)

    return ''

@app.route('/button', methods=['POST'])
def button_handler():
    payload = json.loads(request.form['payload'])['actions'][0]
    action = payload['name']

    if action == 'send':
        attachments = json.loads(payload['value'])['attachments']
        return jsonify(
            response_type='in_channel',
            replace_original=False,
            delete_original=True,
            attachments=attachments
        )
    else:
        return jsonify(
            response_type='in_channel',
            text='',
            replace_original=False,
            delete_original=True
        )
 
@app.route('/finish_auth', methods=['GET', 'POST'])
def post_install():
    auth_code = request.args['code']
    sc = SlackClient("")

    auth_response = sc.api_call(
        'oauth.access',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        code=auth_code
    )

    return "Auth complete!"

@app.errorhandler(500)
def itemNotFound(error):
    return jsonify(
         response_type='ephemeral',
         attachments=[{'fallback': 'sts card error', 'title': "Item '" + request.form['text'] + "' not found!", 'color': 'danger'}]
     )
