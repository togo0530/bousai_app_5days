from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import secrets
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from werkzeug.datastructures import ImmutableMultiDict
import re

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 青森市の実際のJMA警報・注意報エリアコード
# class10 / class20 の両方で利用されるため、両方を対象にする
AREA_CODE = "020010"
AREA_CODES = {AREA_CODE, "0220100"}
# Approximate Aomori City bounding box; coordinates outside it are not mapped.
AOMORI_BOUNDS = (40.4, 41.3, 140.3, 141.3)

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
BROADCASTS_FILE = os.path.join(APP_DIR, 'data', 'broadcasts.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
broadcasts = load_json(BROADCASTS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    save_json_atomically(INSTRUCTIONS_FILE, instructions)


def save_broadcasts():
    """発信履歴をファイルに保存する"""
    save_json_atomically(BROADCASTS_FILE, broadcasts)


def save_json_atomically(path, value):
    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix='.json-', suffix='.tmp'
    )
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8') as data_file:
            json.dump(value, data_file, ensure_ascii=False, indent=2)
            data_file.flush()
            os.fsync(data_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def save_shelters():
    """避難所データをファイルに保存する"""
    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=os.path.dirname(DATA_FILE), prefix='.shelters-', suffix='.tmp'
    )
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8') as shelter_file:
            json.dump(shelters, shelter_file, ensure_ascii=False, indent=2)
            shelter_file.flush()
            os.fsync(shelter_file.fileno())
        os.replace(temporary_path, DATA_FILE)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def get_japan_timestamp():
    return datetime.now(JST).isoformat(timespec="seconds")


INSTRUCTION_RECIPIENTS = ("住民", "職員", "道路管理担当", "消防団", "防災課", "道路管理課")
INSTRUCTION_PRIORITIES = ("高", "中", "低")
INSTRUCTION_STATUSES = ("未対応", "対応中", "完了")
BROADCAST_AUDIENCES = ("住民全体", "避難所利用者", "地域住民")


def instruction_district_options():
    return shelter_district_options() or [AREA_NAME]


def available_shelter_choices():
    return [
        (str(shelter.get("id")), str(shelter.get("name") or "名称未登録"))
        for shelter in shelters
        if isinstance(shelter, dict) and shelter.get("id") is not None
    ]


def sorted_instructions(source):
    priority_order = {"高": 0, "中": 1, "低": 2}

    def sort_key(instruction):
        timestamp = first_value(
            instruction, "created_at", "published_at", "updated_at"
        )
        parsed = parse_instruction_datetime(timestamp)
        priority = str(first_value(instruction, "priority", "urgency") or "中")
        return priority_order.get(priority, 3), -(parsed.timestamp() if parsed else 0)

    return sorted(source, key=sort_key)


def instruction_status_value(instruction):
    value = str(first_value(
        instruction, "instruction_status", "status", "state"
    ) or "未対応")
    if value in INSTRUCTION_STATUSES:
        return value
    if value in {"完了", "解除", "解除済み", "完了済み"}:
        return "完了"
    return "未対応"


def resident_broadcasts(source, limit=None):
    resident = [
        dict(item) for item in source
        if isinstance(item, dict)
        and item.get("target") in BROADCAST_AUDIENCES
    ]
    resident.sort(
        key=lambda item: (
            parse_instruction_datetime(
                first_value(item, "published_at", "created_at")
            ).timestamp()
            if parse_instruction_datetime(
                first_value(item, "published_at", "created_at")
            ) else 0
        ),
        reverse=True,
    )
    return resident[:limit] if limit is not None else resident


def prepare_resident_broadcasts(source):
    prepared = []
    for item in resident_broadcasts(source):
        broadcast = dict(item)
        timestamp = first_value(item, "published_at", "created_at")
        parsed_time = parse_instruction_datetime(timestamp)
        broadcast["display_date"] = (
            parsed_time.strftime("%Y-%m-%d") if parsed_time else ""
        )
        broadcast["display_time"] = (
            parsed_time.strftime("%Y年%m月%d日 %H:%M")
            if parsed_time else str(timestamp or "日時不明")
        )
        prepared.append(broadcast)
    return prepared


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


DISASTER_TYPES = ("津波", "土砂災害", "洪水", "高潮", "地震", "大規模火災")
SEARCH_FACILITIES = ("ペット可", "バリアフリー")
FACILITY_STATES = {"可", "不可", "未確認"}


def shelter_district_options():
    return sorted({
        str(shelter.get("district", "")).strip()
        for shelter in shelters
        if isinstance(shelter, dict) and str(shelter.get("district", "")).strip()
    })


def shelter_disaster_types(shelter):
    values = shelter.get("disaster_types", [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    return list(dict.fromkeys(value for value in values if value in DISASTER_TYPES))


def shelter_facility_state(shelter, facility):
    field_name = {
        "ペット可": "pet_status",
        "バリアフリー": "accessibility_status",
    }[facility]
    value = shelter.get(field_name)
    if field_name in shelter:
        return value if isinstance(value, str) and value in FACILITY_STATES else "未確認"
    legacy_facilities = shelter.get("facilities", [])
    if isinstance(legacy_facilities, list) and facility in legacy_facilities:
        return "可"
    return "未確認"


def prepare_shelter_cards(source):
    prepared = []
    for source_shelter in source:
        if not isinstance(source_shelter, dict):
            continue
        shelter = dict(source_shelter)
        shelter["display_district"] = str(
            shelter.get("district") or shelter.get("address") or "未登録"
        )
        shelter["display_disaster_types"] = shelter_disaster_types(shelter)
        shelter["display_pet_status"] = shelter_facility_state(shelter, "ペット可")
        shelter["display_accessibility_status"] = shelter_facility_state(shelter, "バリアフリー")
        facilities = shelter.get("facilities", [])
        shelter["display_other_facilities"] = (
            facilities if isinstance(facilities, list)
            else [facilities] if isinstance(facilities, str) and facilities
            else []
        )
        prepared.append(shelter)
    return prepared


def search_shelters(query_args):
    selected_disasters = query_args.getlist("disaster_type")
    selected_facilities = query_args.getlist("facility")
    if any(len(query_args.getlist(key)) > 1 for key in ("district", "keyword", "q")):
        return None, "検索条件の指定が不正です。"
    if len(selected_disasters) != len(set(selected_disasters)) or len(selected_facilities) != len(set(selected_facilities)):
        return None, "同じ検索条件を重複して指定できません。"
    keyword = (query_args.get("keyword") or query_args.get("q") or "").strip()
    district = (query_args.get("district") or "").strip()

    invalid_disasters = set(selected_disasters) - set(DISASTER_TYPES)
    invalid_facilities = set(selected_facilities) - set(SEARCH_FACILITIES)
    if invalid_disasters or invalid_facilities:
        return None, "検索条件に不正な値が含まれています。"
    if district and district not in shelter_district_options():
        return None, "登録済みの地区を選択してください。"

    keyword_folded = keyword.casefold()
    results = []
    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        if district and str(shelter.get("district", "")).strip() != district:
            continue
        if keyword_folded and keyword_folded not in str(shelter.get("name", "")).casefold():
            continue
        supported_disasters = set(shelter_disaster_types(shelter))
        if not set(selected_disasters).issubset(supported_disasters):
            continue
        if any(shelter_facility_state(shelter, facility) != "可" for facility in selected_facilities):
            continue
        results.append(shelter)
    return results, None


def normalized_facility_state(value):
    aliases = {
        "可": "可", "対応": "可", "はい": "可", "yes": "可",
        "不可": "不可", "非対応": "不可", "いいえ": "不可", "no": "不可",
        "未確認": "未確認", "": "未確認",
    }
    return aliases.get(str(value).strip().lower())


def first_value(item, *keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def parse_instruction_datetime(value):
    """Parse supported timestamp formats, returning None for unknown values."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for date_format in (
                "%Y年%m月%d日 %H:%M:%S",
                "%Y年%m月%d日 %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y年%m月%d日",
                "%Y/%m/%d",
                "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(text, date_format)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def instruction_datetime_value(instruction):
    values = [
        instruction.get(key)
        for key in ("published_at", "created_at", "received_at", "updated_at")
        if instruction.get(key) not in (None, "")
    ]
    return next((value for value in values if parse_instruction_datetime(value)), values[0] if values else "")


def is_resident_instruction(instruction):
    target = str(first_value(instruction, "target", "audience")).strip().lower()
    return target in {"住民", "住民向け", "市民", "市民向け", "resident", "residents", "public"}


def instruction_type(instruction, content):
    explicit_type = first_value(instruction, "disaster_type", "type")
    if explicit_type:
        return str(explicit_type), False

    categories = (
        ("地震", ("地震", "震度")),
        ("津波", ("津波",)),
        ("大雨・洪水", ("大雨", "豪雨", "洪水", "浸水", "土砂")),
        ("台風", ("台風",)),
        ("大雪", ("大雪", "豪雪", "積雪")),
        ("火災", ("火災", "延焼")),
    )
    for category, keywords in categories:
        if any(keyword in content for keyword in keywords):
            return category, True
    return "その他", True


def prepare_resident_instructions(source):
    """Normalize only public instructions for resident-facing screens."""
    prepared = []
    for original in source:
        if not isinstance(original, dict) or not is_resident_instruction(original):
            continue
        instruction = {}
        content = str(first_value(original, "content", "message", "body"))
        status = str(first_value(original, "instruction_status", "status", "state"))
        timestamp = instruction_datetime_value(original)
        parsed_time = parse_instruction_datetime(timestamp)
        disaster_type, inferred_type = instruction_type(original, content)
        priority = str(first_value(original, "priority", "urgency")).lower()
        closed = status in {"解除", "完了", "解除済み", "完了済み"}
        disaster_icon = {
            "地震": "🌏",
            "津波": "🌊",
            "大雨・洪水": "🌧️",
            "台風": "🌀",
            "大雪": "❄️",
            "火災": "🔥",
        }.get(disaster_type, "⚠️")
        instruction.update({
            "display_content": content or "内容未登録",
            "display_simple_content": str(first_value(
                original, "simple_content", "easy_content", "simple_japanese"
            )),
            "display_status": status or "有効",
            "display_time": (
                parsed_time.strftime("%Y年%m月%d日 %H:%M")
                if parsed_time else str(timestamp or "日時不明")
            ),
            "display_date": parsed_time.strftime("%Y-%m-%d") if parsed_time else "",
            "sort_time": parsed_time.timestamp() if parsed_time else None,
            "display_shelter": str(first_value(original, "shelter", "shelter_name")),
            "display_district": str(first_value(original, "district", "area") or "全地区"),
            "display_recipient": str(first_value(original, "recipient", "target", "audience")),
            "display_priority": str(first_value(original, "priority", "urgency") or "中"),
            "display_type": disaster_type,
            "display_icon": disaster_icon,
            "inferred_type": inferred_type,
            "urgent": (
                not closed and (
                    bool(re.search(r"避難|危険|緊急|直ちに", content))
                    or priority in {"high", "urgent", "critical", "高", "緊急", "最優先"}
                )
            ),
        })
        prepared.append(instruction)

    prepared.sort(
        key=lambda item: (item["sort_time"] is not None, item["sort_time"] or 0),
        reverse=True,
    )
    return prepared


def shelter_open_status(shelter):
    value = str(first_value(shelter, "shelter_status", "open_status", "status")).strip()
    return "開設中" if value == "開設中" else "未開設"


def is_aomori_shelter(shelter):
    location_text = " ".join(
        str(shelter.get(key, "")) for key in ("address", "district", "city")
    )
    if location_text:
        return "青森市" in location_text
    return valid_aomori_shelter_coordinates(shelter) is not None


def valid_aomori_shelter_coordinates(shelter):
    latitude = first_value(shelter, "latitude", "lat")
    longitude = first_value(shelter, "longitude", "lng")
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    south, north, west, east = AOMORI_BOUNDS
    if not (south <= latitude <= north and west <= longitude <= east):
        return None

    location_text = " ".join(
        str(shelter.get(key, "")) for key in ("address", "district", "city")
    )
    if location_text and ("青森市" not in location_text):
        return None
    return latitude, longitude


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class10_items = warning.get("class10Items", [])
        class20_items = warning.get("class20Items", [])
        if not isinstance(class10_items, list):
            class10_items = []
        if not isinstance(class20_items, list):
            class20_items = []

        area_items = class10_items + class20_items
        areas = [
            item for item in area_items
            if isinstance(item, dict)
            and str(item.get("areaCode", "")) in AREA_CODES
        ]
        for area in areas:
            kinds = area.get("kinds", [])
            if not isinstance(kinds, list):
                continue

            for kind in kinds:
                if not isinstance(kind, dict):
                    continue

                status = kind.get("status", "")
                code = str(kind.get("code", ""))
                if status not in ("発表", "継続") or not code or code in seen_codes:
                    continue

                warnings.append({
                    "name": WARNING_CODES.get(
                        code,
                        f"不明な警報・注意報 (コード: {code})"
                    ),
                    "code": code,
                    "status": status
                })
                seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = prepare_resident_instructions(instructions)
    latest_broadcasts = prepare_resident_broadcasts(broadcasts)
    local_shelters = [shelter for shelter in shelters if is_aomori_shelter(shelter)]
    map_shelters = []
    for shelter in local_shelters:
        coordinates = valid_aomori_shelter_coordinates(shelter)
        if coordinates is None:
            continue
        map_shelters.append({
            "name": str(shelter.get("name") or "名称未登録"),
            "latitude": coordinates[0],
            "longitude": coordinates[1],
            "shelter_status": shelter_open_status(shelter),
        })
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        latest_notices=resident_notices[:5],
        latest_broadcasts=latest_broadcasts,
        today_jst=datetime.now(JST).date().isoformat(),
        disaster_types=sorted({item["display_type"] for item in resident_notices}),
        map_shelters=map_shelters,
        unlocated_shelters=[
            str(shelter.get("name") or "名称未登録") for shelter in local_shelters
            if valid_aomori_shelter_coordinates(shelter) is None
        ],
        unlocated_shelter_count=sum(
            1 for shelter in local_shelters
            if valid_aomori_shelter_coordinates(shelter) is None
        ),
        shelter_open_count=sum(
            1 for shelter in local_shelters if shelter_open_status(shelter) == "開設中"
        ),
        shelter_closed_count=sum(
            1 for shelter in local_shelters if shelter_open_status(shelter) != "開設中"
        ),
        last_instruction_update=get_japan_time(),
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    return shelter_form()


@app.route('/shelter_edit/<int:shelter_id>', methods=['GET', 'POST'])
@login_required
def edit_shelter(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        return render_template('shelter_register.html', error=True, message='避難所が見つかりません。', form_data={}), 404
    return shelter_form(shelter)


def shelter_form(existing_shelter=None):
    is_edit = existing_shelter is not None
    if request.method == 'POST':
        action = request.form.get('action', 'confirm')
        if action == 'edit':
            return restore_shelter_draft(existing_shelter, is_edit)
        if action == 'save':
            return commit_shelter_draft(existing_shelter, is_edit)
        if action != 'confirm':
            return render_shelter_form_error(
                '操作を確認できませんでした。もう一度入力してください。',
                shelter_form_values(request.form), is_edit
            )

        form_data, errors = validate_shelter_form(request.form)
        if errors:
            return render_shelter_form_error(
                '入力内容を確認してください。', form_data, is_edit, errors=errors
            )
        duplicate = next(
            (
                shelter for shelter in shelters
                if isinstance(shelter, dict)
                and shelter.get('id') != (existing_shelter or {}).get('id')
                and str(shelter.get('name', '')).strip() == form_data['name']
            ),
            None
        )
        if duplicate:
            return render_shelter_form_error(
                '同じ避難所名がすでに登録されています。',
                shelter_form_values(request.form), is_edit,
                errors={'name': '別の避難所名を入力してください。'}
            )

        token = secrets.token_urlsafe(32)
        session['shelter_draft'] = {
            'token': token,
            'shelter_id': (existing_shelter or {}).get('id'),
            'is_edit': is_edit,
            'form_data': form_data,
        }
        return render_template(
            'shelter_confirm.html', form_data=form_data, token=token,
            is_edit=is_edit,
            shelter_id=(existing_shelter or {}).get('id'),
            disaster_types=DISASTER_TYPES,
        )

    form_data = dict(existing_shelter or {})
    if existing_shelter:
        form_data['disaster_types'] = shelter_disaster_types(existing_shelter)
        form_data['pet_status'] = shelter_facility_state(existing_shelter, 'ペット可')
        form_data['accessibility_status'] = shelter_facility_state(existing_shelter, 'バリアフリー')
        form_data.setdefault(
            'shelter_status',
            existing_shelter.get('open_status')
            or existing_shelter.get('status')
            or shelter_open_status(existing_shelter)
        )
        form_data.setdefault('supplemental_info', '')
    form_data.setdefault('pet_status', '未確認')
    form_data.setdefault('accessibility_status', '未確認')
    form_data.setdefault('supplemental_info', '')
    return render_template(
        'shelter_register.html', form_data=form_data, is_edit=is_edit,
        shelter_id=existing_shelter.get('id') if existing_shelter else None,
        disaster_types=DISASTER_TYPES, facility_states=sorted(FACILITY_STATES),
    )


def shelter_form_values(source):
    def text_value(key):
        value = source.get(key, '')
        return '' if value is None else str(value)

    return {
        'name': text_value('name'),
        'address': text_value('address'),
        'district': text_value('district'),
        'shelter_status': (
            text_value('shelter_status').strip()
            or text_value('status').strip()
            or '未開設'
        ),
        'capacity': text_value('capacity'),
        'current_count': text_value('current_count'),
        'facilities': source.getlist('facilities'),
        'disaster_types': source.getlist('disaster_types'),
        'pet_status': text_value('pet_status').strip() or '未確認',
        'accessibility_status': text_value('accessibility_status').strip() or '未確認',
        'supplemental_info': text_value('supplemental_info'),
    }


def validate_shelter_form(source):
    form_data = shelter_form_values(source)
    validated_data = dict(form_data)
    errors = {}

    for field, label, limit in (
        ('name', '避難所名', 120),
        ('address', '住所', 250),
        ('district', '地区名', 120),
        ('supplemental_info', '補足情報', 2000),
    ):
        normalized_value = form_data[field].strip()
        validated_data[field] = normalized_value
        if field in ('name', 'address') and not normalized_value:
            errors[field] = f'{label}を入力してください。'
        elif len(normalized_value) > limit:
            errors[field] = f'{label}は{limit}文字以内で入力してください。'

    valid_statuses = {'開設中', '未開設', '開設済み', '一時閉鎖', '閉鎖中'}
    if form_data['shelter_status'] not in valid_statuses:
        errors['shelter_status'] = '一覧から有効な開設状況を選択してください。'

    invalid_disasters = set(form_data['disaster_types']) - set(DISASTER_TYPES)
    if invalid_disasters:
        errors['disaster_types'] = '対応災害に不正な選択肢が含まれています。'
    elif len(form_data['disaster_types']) != len(set(form_data['disaster_types'])):
        errors['disaster_types'] = '対応災害の選択肢が重複しています。'

    valid_facilities = set(SEARCH_FACILITIES) | {'高齢者対応', '子ども連れ対応'}
    if set(form_data['facilities']) - valid_facilities:
        errors['facilities'] = '設備に不正な選択肢が含まれています。'
    for field in ('pet_status', 'accessibility_status'):
        if form_data[field] not in FACILITY_STATES:
            errors[field] = '可・不可・未確認から選択してください。'

    for field in ('capacity', 'current_count'):
        value = form_data[field].strip()
        if value and (not re.fullmatch(r'[0-9]+', value, flags=re.ASCII) or len(value) > 9):
            errors[field] = '0以上の整数で入力してください。'
        elif value:
            validated_data[field] = int(value)
        elif field == 'current_count':
            validated_data[field] = 0
        else:
            validated_data[field] = None

    return (form_data if errors else validated_data), errors


def render_shelter_form_error(message, form_data, is_edit, status_code=400, errors=None):
    return render_template(
        'shelter_register.html', error=True, message=message, form_data=form_data,
        is_edit=is_edit,
        shelter_id=request.view_args.get('shelter_id') if request.view_args else None,
        disaster_types=DISASTER_TYPES, facility_states=sorted(FACILITY_STATES),
        field_errors=errors or {},
    ), status_code


def restore_shelter_draft(existing_shelter, is_edit):
    draft = session.get('shelter_draft', {})
    if (
        not isinstance(draft, dict)
        or not secrets.compare_digest(str(draft.get('token', '')), request.form.get('token', ''))
        or draft.get('is_edit') != is_edit
        or draft.get('shelter_id') != (existing_shelter or {}).get('id')
    ):
        return render_shelter_form_error(
            '確認情報の有効期限が切れました。もう一度入力してください。',
            dict(existing_shelter or {}), is_edit, status_code=400
        )
    form_data = draft['form_data']
    session.pop('shelter_draft', None)
    return render_template(
        'shelter_register.html', form_data=form_data, is_edit=is_edit,
        shelter_id=(existing_shelter or {}).get('id'),
        disaster_types=DISASTER_TYPES, facility_states=sorted(FACILITY_STATES),
    )


def commit_shelter_draft(existing_shelter, is_edit):
    draft = session.get('shelter_draft', {})
    supplied_token = request.form.get('token', '')
    if (
        not isinstance(draft, dict)
        or not supplied_token
        or not secrets.compare_digest(str(draft.get('token', '')), supplied_token)
        or draft.get('is_edit') != is_edit
        or draft.get('shelter_id') != (existing_shelter or {}).get('id')
        or not isinstance(draft.get('form_data'), dict)
    ):
        return render_shelter_form_error(
            '確認情報を確認できませんでした。もう一度入力してください。',
            dict(existing_shelter or {}), is_edit, status_code=400
        )

    form_data, errors = validate_shelter_form(
        ImmutableMultiDict(draft['form_data'])
    )
    if errors:
        session.pop('shelter_draft', None)
        return render_shelter_form_error(
            '入力内容を再確認してください。', form_data, is_edit, errors=errors
        )
    duplicate = next(
        (
            shelter for shelter in shelters
            if isinstance(shelter, dict)
            and shelter.get('id') != (existing_shelter or {}).get('id')
            and str(shelter.get('name', '')).strip() == form_data['name']
        ),
        None
    )
    if duplicate:
        session.pop('shelter_draft', None)
        return render_shelter_form_error(
            '同じ避難所名がすでに登録されています。', form_data, is_edit,
            errors={'name': '別の避難所名を入力してください。'}
        )

    updated_shelter = {
        **(existing_shelter or {}),
        **form_data,
        'status': form_data['shelter_status'],
        'shelter_status': form_data['shelter_status'],
    }
    if is_edit:
        shelter_index = next(
            index for index, shelter in enumerate(shelters)
            if shelter is existing_shelter
        )
        previous_shelter = shelters[shelter_index]
        shelters[shelter_index] = updated_shelter
    else:
        updated_shelter['id'] = max(
            (s.get('id', 0) for s in shelters if isinstance(s, dict) and isinstance(s.get('id'), int)),
            default=0
        ) + 1
        shelters.append(updated_shelter)
        shelter_index = len(shelters) - 1
        previous_shelter = None
    try:
        save_shelters()
    except (OSError, TypeError, ValueError):
        if previous_shelter is None:
            shelters.pop(shelter_index)
        else:
            shelters[shelter_index] = previous_shelter
        return render_template(
            'shelter_confirm.html', form_data=form_data,
            token=draft['token'], is_edit=is_edit,
            shelter_id=(existing_shelter or {}).get('id'),
            disaster_types=DISASTER_TYPES,
            error='避難所情報を保存できませんでした。再試行してください。',
        ), 500

    session.pop('shelter_draft', None)
    return render_template(
        'shelter_register.html', success=True,
        message='避難所情報を更新しました。' if is_edit else '避難所を登録しました。',
        form_data=updated_shelter, is_edit=is_edit, shelter_id=updated_shelter.get('id'),
        disaster_types=DISASTER_TYPES, facility_states=sorted(FACILITY_STATES),
    )

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template(
        'shelter_search.html',
        districts=shelter_district_options(),
        disaster_types=DISASTER_TYPES,
        facility_options=SEARCH_FACILITIES,
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=prepare_shelter_cards(shelters),
        is_all_shelters=True,
    )


@app.route('/shelter_delete/<int:shelter_id>', methods=['POST'])
@login_required
def delete_shelter(shelter_id):
    global shelters
    previous_shelters = shelters
    shelters = [s for s in shelters if s.get('id') != shelter_id]
    try:
        save_shelters()
    except (OSError, TypeError, ValueError):
        shelters = previous_shelters
        return render_template(
            'search_results.html',
            results=prepare_shelter_cards(shelters),
            is_all_shelters=True,
            search_error='避難所情報を保存できなかったため、削除を取り消しました。',
        ), 500
    return redirect(url_for('all_shelters'))


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    context = board_context()
    if request.method == 'GET':
        return render_template('board.html', **context)
    expected_csrf = session.get('board_csrf_token', '')
    supplied_csrf = request.form.get('csrf_token', '')
    if (
        not expected_csrf
        or not supplied_csrf
        or not secrets.compare_digest(expected_csrf, supplied_csrf)
    ):
        return render_template(
            'board.html', **board_context(error_message='画面の有効期限が切れました。再読み込みしてください。')
        ), 400

    action = request.form.get('action', '')
    if action == 'register_instruction':
        return register_instruction()
    if action == 'update_instruction_status':
        return update_instruction_status()
    if action == 'preview_broadcast':
        return preview_broadcast()
    if action == 'publish_broadcast':
        return publish_broadcast()
    if action == 'cancel_broadcast':
        draft = session.get('broadcast_draft', {})
        session.pop('broadcast_draft', None)
        return render_template(
            'board.html',
            **board_context(
                success_message='発信内容の確認を取り消しました。入力内容を修正できます。',
                broadcast_form=draft.get('data', {}) if isinstance(draft, dict) else {},
            )
        )
    return render_template(
        'board.html', error_message='操作を確認できませんでした。', **context
    ), 400


@app.route('/board', methods=['POST'])
@login_required
def board_post():
    return board()


def board_context(**extra):
    ordered = sorted_instructions(instructions)
    csrf_token = session.get("board_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["board_csrf_token"] = csrf_token
    return {
        "instructions": ordered,
        "broadcasts": resident_broadcasts(broadcasts),
        "districts": instruction_district_options(),
        "shelter_choices": available_shelter_choices(),
        "recipients": INSTRUCTION_RECIPIENTS,
        "priorities": INSTRUCTION_PRIORITIES,
        "instruction_statuses": INSTRUCTION_STATUSES,
        "broadcast_audiences": BROADCAST_AUDIENCES,
        "instruction_status_value": instruction_status_value,
        "csrf_token": csrf_token,
        "jst_now": get_japan_time(),
        **extra,
    }


def validate_instruction_form(form):
    data = {
        "district": form.get("district", "").strip(),
        "recipient": form.get("recipient", "").strip(),
        "priority": form.get("priority", "").strip(),
        "shelter_id": form.get("shelter_id", "").strip(),
        "content": form.get("content", "").strip(),
        "simple_content": form.get("simple_content", "").strip(),
    }
    errors = {}
    if data["district"] not in instruction_district_options():
        errors["district"] = "登録済みの地区を選択してください。"
    if data["recipient"] not in INSTRUCTION_RECIPIENTS:
        errors["recipient"] = "有効な宛先を選択してください。"
    if data["priority"] not in INSTRUCTION_PRIORITIES:
        errors["priority"] = "緊急度を選択してください。"
    if not data["content"]:
        errors["content"] = "指示内容を入力してください。"
    elif len(data["content"]) > 2000:
        errors["content"] = "指示内容は2000文字以内で入力してください。"
    if len(data["simple_content"]) > 2000:
        errors["simple_content"] = "やさしい日本語の指示は2000文字以内で入力してください。"
    shelter = None
    if data["shelter_id"]:
        shelter = next(
            (
                item for item in shelters
                if isinstance(item, dict)
                and str(item.get("id")) == data["shelter_id"]
            ),
            None,
        )
        if shelter is None:
            errors["shelter_id"] = "登録済みの避難所を選択してください。"
    return data, shelter, errors


def register_instruction():
    data, shelter, errors = validate_instruction_form(request.form)
    if errors:
        return render_template(
            'board.html', **board_context(
                error_message='入力内容を確認してください。',
                instruction_errors=errors,
                instruction_form=request.form,
            )
        ), 400

    now = get_japan_timestamp()
    record = {
        "id": secrets.token_hex(12),
        "target": data["recipient"],
        "recipient": data["recipient"],
        "district": data["district"],
        "content": data["content"],
        "simple_content": data["simple_content"],
        "shelter": shelter.get("name", "") if shelter else "",
        "shelter_id": shelter.get("id") if shelter else None,
        "priority": data["priority"],
        "instruction_status": "未対応",
        "created_at": now,
        "updated_at": now,
    }
    instructions.append(record)
    try:
        save_instructions()
    except (OSError, TypeError, ValueError):
        instructions.pop()
        return render_template(
            'board.html', **board_context(
                error_message='指示を保存できませんでした。入力内容を保持しました。再試行してください。',
                instruction_form=request.form,
            )
        ), 500
    return render_template(
        'board.html', **board_context(
            success_message='指示を登録しました。対応状況は「未対応」です。'
        )
    )


def update_instruction_status():
    instruction_id = request.form.get("instruction_id", "")
    status = request.form.get("instruction_status", "")
    if status not in INSTRUCTION_STATUSES:
        return render_template(
            'board.html', **board_context(error_message='対応状況の値が不正です。')
        ), 400
    instruction = next(
        (
            item for item in instructions
            if isinstance(item, dict) and str(item.get("id")) == instruction_id
        ),
        None,
    )
    if instruction is None:
        return render_template(
            'board.html', **board_context(error_message='指示が見つかりません。')
        ), 404

    previous_status = instruction.get("instruction_status")
    previous_updated_at = instruction.get("updated_at")
    instruction["instruction_status"] = status
    instruction["updated_at"] = get_japan_timestamp()
    try:
        save_instructions()
    except (OSError, TypeError, ValueError):
        if previous_status is None:
            instruction.pop("instruction_status", None)
        else:
            instruction["instruction_status"] = previous_status
        if previous_updated_at is None:
            instruction.pop("updated_at", None)
        else:
            instruction["updated_at"] = previous_updated_at
        return render_template(
            'board.html', **board_context(error_message='対応状況を保存できませんでした。')
        ), 500
    return render_template(
        'board.html', **board_context(success_message='対応状況を更新しました。')
    )


def validate_broadcast_form(form):
    data = {
        "target": form.get("broadcast_target", "").strip(),
        "district": form.get("broadcast_district", "").strip(),
        "shelter_id": form.get("broadcast_shelter_id", "").strip(),
        "title": form.get("title", "").strip(),
        "content": form.get("broadcast_content", "").strip(),
        "simple_content": form.get("broadcast_simple_content", "").strip(),
    }
    errors = {}
    if data["target"] not in BROADCAST_AUDIENCES:
        errors["broadcast_target"] = "発信対象を選択してください。"
    if data["target"] == "地域住民" and data["district"] not in instruction_district_options():
        errors["broadcast_district"] = "登録済みの地区を選択してください。"
    shelter = None
    if data["target"] == "避難所利用者" or data["shelter_id"]:
        shelter = next(
            (
                item for item in shelters
                if isinstance(item, dict)
                and str(item.get("id")) == data["shelter_id"]
            ),
            None,
        )
        if shelter is None:
            errors["broadcast_shelter_id"] = "登録済みの避難所を選択してください。"
    if not data["title"]:
        errors["title"] = "発信タイトルを入力してください。"
    elif len(data["title"]) > 120:
        errors["title"] = "発信タイトルは120文字以内で入力してください。"
    if not data["content"]:
        errors["broadcast_content"] = "発信内容を入力してください。"
    elif len(data["content"]) > 4000:
        errors["broadcast_content"] = "発信内容は4000文字以内で入力してください。"
    if len(data["simple_content"]) > 4000:
        errors["broadcast_simple_content"] = "やさしい日本語の発信内容は4000文字以内で入力してください。"
    data["shelter"] = shelter.get("name", "") if shelter else ""
    return data, errors


def preview_broadcast():
    data, errors = validate_broadcast_form(request.form)
    if errors:
        return render_template(
            'board.html', **board_context(
                error_message='発信内容を確認してください。',
                broadcast_errors=errors,
                broadcast_form=request.form,
            )
        ), 400
    token = secrets.token_urlsafe(32)
    session["broadcast_draft"] = {"token": token, "data": data}
    return render_template(
        'board.html', **board_context(
            broadcast_preview=data, broadcast_token=token,
            broadcast_form=request.form,
        )
    )


def publish_broadcast():
    draft = session.get("broadcast_draft", {})
    supplied_token = request.form.get("token", "")
    if (
        not isinstance(draft, dict)
        or not supplied_token
        or not secrets.compare_digest(str(draft.get("token", "")), supplied_token)
        or not isinstance(draft.get("data"), dict)
    ):
        return render_template(
            'board.html', **board_context(error_message='発信内容を確認できません。内容を再度確認してください。')
        ), 400
    data, errors = validate_broadcast_form(
        ImmutableMultiDict({
            "broadcast_target": draft["data"].get("target", ""),
            "broadcast_district": draft["data"].get("district", ""),
            "broadcast_shelter_id": draft["data"].get("shelter_id", ""),
            "title": draft["data"].get("title", ""),
            "broadcast_content": draft["data"].get("content", ""),
            "broadcast_simple_content": draft["data"].get("simple_content", ""),
        })
    )
    if errors:
        session.pop("broadcast_draft", None)
        return render_template(
            'board.html', **board_context(
                error_message='発信内容を再確認してください。',
                broadcast_errors=errors, broadcast_form=data,
            )
        ), 400
    record = {
        "id": secrets.token_hex(12),
        "target": data["target"],
        "district": data["district"] if data["target"] == "地域住民" else "",
        "shelter": data["shelter"],
        "title": data["title"],
        "content": data["content"],
        "simple_content": data["simple_content"],
        "published_at": get_japan_timestamp(),
    }
    broadcasts.append(record)
    try:
        save_broadcasts()
    except (OSError, TypeError, ValueError):
        broadcasts.pop()
        return render_template(
            'board.html', **board_context(
                error_message='発信履歴を保存できませんでした。再試行してください。',
                broadcast_preview=data, broadcast_token=draft["token"],
                broadcast_form=data,
            )
        ), 500
    session.pop("broadcast_draft", None)
    return render_template(
        'board.html', **board_context(
            success_message='住民向け情報を発信しました。'
        )
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results, error = search_shelters(request.args)
    if error:
        return render_template(
            'search_results.html',
            results=[],
            is_all_shelters=False,
            search_error=error,
            search_query=request.query_string.decode('utf-8'),
        ), 400
    return render_template(
        'search_results.html',
        results=prepare_shelter_cards(results),
        is_all_shelters=False,
        search_query=request.query_string.decode('utf-8'),
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())


@app.route('/api/disaster_info')
def api_disaster_info():
    """Return the latest saved resident-facing instructions for home refresh."""
    try:
        with open(INSTRUCTIONS_FILE, encoding='utf-8') as instructions_file:
            saved_instructions = json.load(instructions_file)
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "保存済みの指示データを読み込めませんでした。"}), 500
    if not isinstance(saved_instructions, list):
        return jsonify({"error": "指示データを読み込めませんでした。"}), 500
    try:
        with open(BROADCASTS_FILE, encoding='utf-8') as broadcasts_file:
            saved_broadcasts = json.load(broadcasts_file)
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "発信履歴を読み込めませんでした。"}), 500
    if not isinstance(saved_broadcasts, list):
        return jsonify({"error": "発信履歴を読み込めませんでした。"}), 500
    prepared = prepare_resident_instructions(saved_instructions)
    return jsonify({
        "instructions": prepared,
        "broadcasts": prepare_resident_broadcasts(saved_broadcasts),
        "updated_at": get_japan_time(),
        "today_jst": datetime.now(JST).date().isoformat(),
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
