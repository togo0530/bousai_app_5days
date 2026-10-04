import app as app_module
import json
import re


def confirm_and_save(client, path, data):
    confirmation = client.post(path, data=data)
    assert confirmation.status_code == 200
    html = confirmation.get_data(as_text=True)
    assert '登録内容を確認' in html or '更新内容を確認' in html
    token_match = re.search(r'name="token" value="([^"]+)"', html)
    assert token_match
    return client.post(path, data={'action': 'save', 'token': token_match.group(1)})


def board_csrf_token(client):
    client.get('/board')
    with client.session_transaction() as session:
        return session['board_csrf_token']


def test_register_shelter_with_name_success(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['logged_in'] = True
        session['username'] = 'admin'

    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '御所見小学校'},
        {'id': 2, 'name': '片瀬小学校'},
    ])

    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    response = confirm_and_save(client, '/shelter_register', {
        'name': '新しい避難所',
        'address': '青森市新町1-1',
        'status': '開設中'
    })

    assert response.status_code == 200
    assert '避難所を登録しました' in response.get_data(as_text=True)
    assert any(shelter['name'] == '新しい避難所' and shelter['address'] == '青森市新町1-1' for shelter in app_module.shelters)


def test_delete_shelter_success(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['logged_in'] = True
        session['username'] = 'admin'

    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '御所見小学校'},
        {'id': 2, 'name': '片瀬小学校'},
    ])

    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    response = client.post('/shelter_delete/2')

    assert response.status_code == 302
    assert all(shelter['id'] != 2 for shelter in app_module.shelters)


def test_register_shelter_with_address_and_status(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['logged_in'] = True
        session['username'] = 'admin'

    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '御所見小学校', 'address': '青森市大字...', 'status': '開設済み'},
    ])

    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    response = confirm_and_save(client, '/shelter_register', {
        'name': '新しい避難所',
        'address': '青森市新町1-2-3',
        'status': '開設中'
    })

    assert response.status_code == 200
    assert '青森市新町1-2-3' in response.get_data(as_text=True)
    assert any(shelter['address'] == '青森市新町1-2-3' and shelter['status'] == '開設中' for shelter in app_module.shelters)


def test_register_shelter_saves_open_status_and_coordinates(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
        session['username'] = 'admin'
    monkeypatch.setattr(app_module, 'shelters', [])
    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    response = confirm_and_save(client, '/shelter_register', {
        'name': '青森市避難所',
        'address': '青森市新町1-1',
        'shelter_status': '開設中',
        'latitude': '40.8244',
        'longitude': '140.7400',
    })

    assert response.status_code == 200
    assert app_module.shelters[0]['shelter_status'] == '開設中'
    assert app_module.shelters[0]['latitude'] == 40.8244
    assert app_module.shelters[0]['longitude'] == 140.7400


def test_home_shows_resident_disaster_info_and_only_aomori_markers(monkeypatch):
    monkeypatch.setattr(app_module, 'instructions', [
        {
            'target': '住民', 'content': '危険です。直ちに避難してください',
            'status': '発表中', 'created_at': '2026年10月03日 12:00',
        },
        {
            'target': '防災課', 'content': '内部確認してください',
            'status': '発表中', 'created_at': '2026年10月04日 12:00',
        },
        {
            'audience': '住民向け', 'message': '解除済みのお知らせ',
            'instruction_status': '解除', 'published_at': 'not-a-date',
        },
    ])
    monkeypatch.setattr(app_module, 'shelters', [
        {
            'name': '青森避難所', 'address': '青森市新町',
            'latitude': 40.8244, 'longitude': 140.7400,
            'shelter_status': '開設中',
        },
        {
            'name': '未開設避難所', 'address': '青森市本町',
            'latitude': 40.8250, 'longitude': 140.7410,
        },
        {
            'name': '座標未登録', 'address': '青森市浪岡',
        },
        {
            'name': '他地域', 'address': '藤沢市本町',
            'latitude': 40.8244, 'longitude': 140.7400,
        },
    ])

    response = app_module.app.test_client().get('/')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '緊急のお知らせ' in html
    assert '新着指示' in html
    assert '災害情報一覧' in html
    assert '危険です。直ちに避難してください' in html
    assert '内部確認してください' not in html
    assert '位置情報が登録されていません' in html
    assert '座標未登録' in html
    assert '他地域' not in html
    assert '現在地を表示' in html
    assert 'aria-current="page"' in html
    assert html.index('危険です。直ちに避難してください') < html.index('解除済みのお知らせ')


def test_disaster_info_api_reloads_saved_resident_instructions(tmp_path, monkeypatch):
    saved_path = tmp_path / 'instructions.json'
    saved_path.write_text(json.dumps([
        {'target': '防災課', 'content': '内部指示'},
        {
            'target': '住民', 'content': '新しい指示',
            'created_at': '2026-10-04T03:00:00Z',
        },
    ], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app_module, 'INSTRUCTIONS_FILE', str(saved_path))
    monkeypatch.setattr(app_module, 'instructions', [])

    response = app_module.app.test_client().get('/api/disaster_info')
    data = response.get_json()
    assert response.status_code == 200
    assert [item['display_content'] for item in data['instructions']] == ['新しい指示']
    assert data['instructions'][0]['display_time'] == '2026年10月04日 12:00'


def test_navigation_does_not_bypass_protected_pages():
    client = app_module.app.test_client()
    for path in ('/shelter_register', '/board'):
        response = client.get(path)
        assert response.status_code == 302
        assert '/login' in response.headers['Location']


def test_common_navigation_is_rendered_and_marks_current_page():
    client = app_module.app.test_client()
    paths = (
        '/login', '/', '/shelter_search', '/search_results',
        '/all_shelters', '/shelter_register', '/board',
    )
    for path in paths:
        if path in ('/shelter_register', '/board'):
            with client.session_transaction() as session:
                session['logged_in'] = True
                session['username'] = 'admin'
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="main-navigation"' in html
        assert 'aria-controls="main-navigation"' in html
        assert html.count('aria-current="page">') == 1


def test_search_results_include_capacity_and_facilities(monkeypatch):
    client = app_module.app.test_client()

    monkeypatch.setattr(app_module, 'shelters', [
        {
            'id': 1,
            'name': '新しい避難所',
            'address': '青森市新町1-2-3',
            'status': '開設中',
            'capacity': 80,
            'current_count': 50,
            'facilities': ['ペット可', 'バリアフリー']
        }
    ])

    response = client.get('/search_results')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '80' in html
    assert '50' in html
    assert 'ペット可' in html or '🐾' in html
    assert 'バリアフリー' in html or '♿' in html


def test_shelter_search_combines_and_filters_and_preserves_conditions(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [
        {
            'id': 1, 'name': '中央小学校', 'district': '中央地区', 'address': '住所1',
            'disaster_types': ['洪水', '地震'], 'pet_status': '可',
            'accessibility_status': '可',
        },
        {
            'id': 2, 'name': '東小学校', 'district': '東地区', 'address': '住所2',
            'disaster_types': ['洪水'], 'pet_status': '可',
            'accessibility_status': '不可',
        },
        {
            'id': 3, 'name': '西中学校', 'district': '中央地区', 'address': '住所3',
            'disaster_types': ['洪水', '地震'], 'pet_status': '未確認',
            'accessibility_status': '可',
        },
        {
            'id': 4, 'name': '未登録小学校', 'district': '中央地区', 'address': '住所4',
        },
    ])

    client = app_module.app.test_client()
    response = client.get(
        '/search_results?keyword=%E5%B0%8F%E5%AD%A6%E6%A0%A1&district=%E4%B8%AD%E5%A4%AE%E5%9C%B0%E5%8C%BA'
        '&disaster_type=%E6%B4%AA%E6%B0%B4&disaster_type=%E5%9C%B0%E9%9C%87'
        '&facility=%E3%83%9A%E3%83%83%E3%83%88%E5%8F%AF&facility=%E3%83%90%E3%83%AA%E3%82%A2%E3%83%95%E3%83%AA%E3%83%BC'
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '中央小学校' in html
    assert '東小学校' not in html
    assert '西中学校' not in html
    assert '未登録小学校' not in html
    assert '検索条件を変更する' in html
    assert '検索条件をクリア' in html

    partial_match = client.get('/search_results?keyword=%E5%B0%8F%E5%AD%A6%E6%A0%A1')
    assert '中央小学校' in partial_match.get_data(as_text=True)
    assert '東小学校' in partial_match.get_data(as_text=True)
    assert '西中学校' not in partial_match.get_data(as_text=True)


def test_shelter_search_blank_and_facility_conditions_treat_unknown_safely(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': 'A', 'facilities': ['ペット可']},
        {'id': 2, 'name': 'B', 'pet_status': '不可'},
        {'id': 3, 'name': 'C'},
    ])
    client = app_module.app.test_client()

    blank_search = client.get('/search_results')
    assert blank_search.status_code == 200
    assert all(f'>{name}<' in blank_search.get_data(as_text=True) for name in 'ABC')

    pet_search = client.get('/search_results?facility=%E3%83%9A%E3%83%83%E3%83%88%E5%8F%AF')
    pet_html = pet_search.get_data(as_text=True)
    assert '<h3>A</h3>' in pet_html
    assert '<h3>B</h3>' not in pet_html
    assert '<h3>C</h3>' not in pet_html

    all_shelters = client.get('/all_shelters').get_data(as_text=True)
    assert all_shelters.count('class="shelter-card"') == 3
    assert '未確認' in all_shelters
    assert '不可' in all_shelters


def test_shelter_search_rejects_invalid_disasters_facilities_and_districts(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [{'name': '施設', 'district': '中央地区'}])
    client = app_module.app.test_client()
    for query in (
        'disaster_type=unknown',
        'facility=unknown',
        'district=not-registered',
    ):
        response = client.get('/search_results?' + query)
        assert response.status_code == 400
        assert '検索条件' in response.get_data(as_text=True)


def test_shelter_search_form_uses_registered_districts_and_retains_filters(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [
        {'name': '施設A', 'district': '中央地区'},
        {'name': '施設B', 'district': '中央地区'},
        {'name': '施設C', 'district': '東地区'},
        {'name': '施設D'},
    ])
    response = app_module.app.test_client().get(
        '/shelter_search?district=%E4%B8%AD%E5%A4%AE%E5%9C%B0%E5%8C%BA'
        '&disaster_type=%E6%B4%AA%E6%B0%B4&facility=%E3%83%9A%E3%83%83%E3%83%88%E5%8F%AF'
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count('value="中央地区"') == 1
    assert html.count('value="東地区"') == 1
    assert 'value="中央地区" selected' in html
    assert 'value="洪水" checked' in html
    assert 'value="ペット可" checked' in html
    assert 'value="施設D"' not in html


def test_shelter_registration_validates_and_persists_search_fields(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [])
    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)
    form = {
        'name': '中央小学校', 'address': '青森市新町1-1', 'district': '中央地区',
        'shelter_status': '未開設', 'disaster_types': ['津波', '地震'],
        'pet_status': '未確認', 'accessibility_status': '不可',
    }

    response = client.post('/shelter_register', data={**form, 'disaster_types': ['不正な種別']})
    assert response.status_code == 400
    assert '中央小学校' in response.get_data(as_text=True)
    assert app_module.shelters == []

    response = confirm_and_save(client, '/shelter_register', form)
    assert response.status_code == 200
    shelter = app_module.shelters[0]
    assert shelter['district'] == '中央地区'
    assert shelter['disaster_types'] == ['津波', '地震']
    assert shelter['pet_status'] == '未確認'
    assert shelter['accessibility_status'] == '不可'


def test_shelter_edit_updates_existing_search_fields_and_requires_login(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [{
        'id': 1, 'name': '旧施設', 'address': '住所', 'district': '中央地区',
        'facilities': [], 'disaster_types': [],
    }])
    client = app_module.app.test_client()
    assert client.get('/shelter_edit/1').status_code == 302
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    response = confirm_and_save(client, '/shelter_edit/1', {
        'name': '更新施設', 'address': '住所2', 'district': '東地区',
        'shelter_status': '開設中', 'disaster_types': ['洪水'],
        'pet_status': '不可', 'accessibility_status': '可',
    })
    assert response.status_code == 200
    assert app_module.shelters[0]['name'] == '更新施設'
    assert app_module.shelters[0]['district'] == '東地区'
    assert app_module.shelters[0]['disaster_types'] == ['洪水']
    assert app_module.shelters[0]['pet_status'] == '不可'
    assert app_module.shelters[0]['accessibility_status'] == '可'


def test_shelter_registration_does_not_claim_success_when_save_fails(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [])

    def fail_to_save():
        raise OSError('disk unavailable')

    monkeypatch.setattr(app_module, 'save_shelters', fail_to_save)
    confirmation = client.post('/shelter_register', data={
        'name': '保存失敗施設',
        'address': '住所',
        'shelter_status': '未開設',
    })
    token = re.search(
        r'name="token" value="([^"]+)"',
        confirmation.get_data(as_text=True)
    ).group(1)
    response = client.post(
        '/shelter_register',
        data={'action': 'save', 'token': token},
    )
    assert response.status_code == 500
    assert '保存できませんでした' in response.get_data(as_text=True)
    assert app_module.shelters == []


def test_shelter_registration_confirmation_does_not_save_until_explicit_commit(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [])
    saved = []
    monkeypatch.setattr(app_module, 'save_shelters', lambda: saved.append(True))
    form = {
        'name': '確認待ち施設', 'address': '住所', 'capacity': '0',
        'disaster_types': ['洪水'], 'pet_status': '未確認',
        'accessibility_status': '可', 'shelter_status': '開設中',
        'supplemental_info': '設備の補足',
    }

    confirmation = client.post('/shelter_register', data=form)
    html = confirmation.get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', html).group(1)
    assert confirmation.status_code == 200
    assert '確認待ち施設' in html
    assert '設備の補足' in html
    assert app_module.shelters == []
    assert saved == []

    correction = client.post(
        '/shelter_register',
        data={'action': 'edit', 'token': token},
    )
    assert correction.status_code == 200
    assert 'value="確認待ち施設"' in correction.get_data(as_text=True)
    assert app_module.shelters == []

    confirmation = client.post('/shelter_register', data=form)
    token = re.search(
        r'name="token" value="([^"]+)"',
        confirmation.get_data(as_text=True)
    ).group(1)
    committed = client.post(
        '/shelter_register',
        data={
            'action': 'save',
            'token': token,
            'name': 'hidden field tampering',
        },
    )
    assert committed.status_code == 200
    assert len(app_module.shelters) == 1
    assert app_module.shelters[0]['name'] == '確認待ち施設'
    assert saved == [True]
    duplicate_submit = client.post(
        '/shelter_register',
        data={'action': 'save', 'token': token},
    )
    assert duplicate_submit.status_code == 400
    assert len(app_module.shelters) == 1


def test_shelter_registration_validates_required_lengths_capacity_and_duplicates(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '既存避難所', 'address': '住所'}
    ])
    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)

    for invalid_form, expected in (
        ({'name': '　', 'address': '住所'}, '避難所名を入力してください'),
        ({'name': '新施設', 'address': ' '}, '住所を入力してください'),
        ({'name': '新施設', 'address': '住所', 'capacity': '-1'}, '0以上の整数'),
        ({'name': '新施設', 'address': '住所', 'capacity': '1.5'}, '0以上の整数'),
        ({'name': '新施設', 'address': '住所', 'capacity': '一人'}, '0以上の整数'),
        ({'name': '新施設', 'address': '住所', 'name_padding': 'x' * 121}, '120文字以内'),
        ({'name': '新施設', 'address': '住所', 'address_padding': 'x' * 251}, '250文字以内'),
        ({'name': '新施設', 'address': '住所', 'supplemental_info': 'x' * 2001}, '2000文字以内'),
    ):
        form = {
            'name': invalid_form.get('name', '新施設'),
            'address': invalid_form.get('address', '住所'),
            'capacity': invalid_form.get('capacity', ''),
            'supplemental_info': invalid_form.get('supplemental_info', ''),
        }
        if 'name_padding' in invalid_form:
            form['name'] = invalid_form['name_padding']
        if 'address_padding' in invalid_form:
            form['address'] = invalid_form['address_padding']
        response = client.post('/shelter_register', data=form)
        assert response.status_code == 400
        assert expected in response.get_data(as_text=True)

    response = client.post('/shelter_register', data={
        'name': '  既存避難所  ', 'address': '住所'
    })
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert '同じ避難所名がすでに登録されています' in html
    assert 'value="  既存避難所  "' in html

    response = confirm_and_save(client, '/shelter_register', {
        'name': '新しい施設', 'address': '住所', 'capacity': '0'
    })
    assert response.status_code == 200
    assert app_module.shelters[-1]['capacity'] == 0


def test_editing_own_name_is_allowed_and_keeps_existing_fields(monkeypatch):
    existing = {
        'id': 1, 'name': '既存避難所', 'address': '住所', 'created_at': 'created',
        'supplemental_info': 'old note', 'latitude': 40.8, 'longitude': 140.8,
        'status': '開設済み',
    }
    monkeypatch.setattr(app_module, 'shelters', [existing])
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'save_shelters', lambda: None)
    edit_form = client.get('/shelter_edit/1').get_data(as_text=True)
    assert 'value="開設済み" selected' in edit_form

    response = confirm_and_save(client, '/shelter_edit/1', {
        'name': '既存避難所', 'address': '新しい住所',
        'latitude': '40.8', 'longitude': '140.8',
        'supplemental_info': 'updated note',
    })
    assert response.status_code == 200
    assert len(app_module.shelters) == 1
    assert app_module.shelters[0]['id'] == 1
    assert app_module.shelters[0]['created_at'] == 'created'


def test_shelter_save_persists_atomically_to_existing_json_format(tmp_path, monkeypatch):
    data_file = tmp_path / 'shelters.json'
    monkeypatch.setattr(app_module, 'DATA_FILE', str(data_file))
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 17, 'name': '保存施設', 'address': '住所'}
    ])

    app_module.save_shelters()

    assert json.loads(data_file.read_text(encoding='utf-8')) == app_module.shelters
    assert list(tmp_path.iterdir()) == [data_file]


def test_shelters_api_keeps_legacy_list_and_not_found_responses(monkeypatch):
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '中央小学校', 'district': '中央地区'},
        {'id': 2, 'name': '東小学校', 'district': '東地区'},
    ])
    client = app_module.app.test_client()

    filtered = client.get('/shelters?district=%E4%B8%AD%E5%A4%AE%E5%9C%B0%E5%8C%BA')
    assert filtered.status_code == 200
    assert filtered.get_json() == [{'id': 1, 'name': '中央小学校', 'district': '中央地区'}]
    not_found = client.get('/shelters?district=not-found')
    assert not_found.status_code == 404
    assert not_found.get_json() == {'error': 'No shelters found'}


def test_board_requires_login_while_home_remains_public():
    client = app_module.app.test_client()
    assert client.get('/').status_code == 200
    response = client.get('/board')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    response = client.post('/board', data={'action': 'register_instruction'})
    assert response.status_code == 302


def test_instruction_registration_validates_saves_and_sorts(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '青森小学校', 'district': '中央地区'}
    ])
    monkeypatch.setattr(app_module, 'instructions', [])
    timestamps = iter((
        '2026-10-04T05:00:00+09:00',
        '2026-10-04T05:01:00+09:00',
        '2026-10-04T05:02:00+09:00',
    ))
    monkeypatch.setattr(app_module, 'get_japan_timestamp', lambda: next(timestamps))
    saved = []
    monkeypatch.setattr(app_module, 'save_instructions', lambda: saved.append(True))
    csrf = board_csrf_token(client)

    invalid = client.post('/board', data={
        'csrf_token': csrf, 'action': 'register_instruction',
        'district': '不正な地区', 'recipient': '住民', 'priority': '高',
        'content': '避難してください',
    })
    assert invalid.status_code == 400
    assert app_module.instructions == []

    forms = [
        ('低', '低緊急度'),
        ('高', '高緊急度'),
        ('中', '中緊急度'),
    ]
    for priority, content in forms:
        response = client.post('/board', data={
            'csrf_token': csrf, 'action': 'register_instruction',
            'district': '中央地区', 'recipient': '住民', 'priority': priority,
            'shelter_id': '1', 'content': content,
            'simple_content': 'やさしい指示',
        })
        assert response.status_code == 200

    assert len(saved) == 3
    assert all(record['instruction_status'] == '未対応' for record in app_module.instructions)
    assert all(record['created_at'] == record['updated_at'] for record in app_module.instructions)
    ordered = app_module.sorted_instructions(app_module.instructions)
    assert [record['content'] for record in ordered] == ['高緊急度', '中緊急度', '低緊急度']
    assert ordered[0]['shelter'] == '青森小学校'
    assert ordered[0]['target'] == '住民'


def test_instruction_status_updates_and_rolls_back_on_save_failure(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    record = {
        'id': 'instruction-1', 'target': '住民', 'content': '指示',
        'instruction_status': '未対応', 'updated_at': 'old',
    }
    monkeypatch.setattr(app_module, 'instructions', [record])
    monkeypatch.setattr(app_module, 'save_instructions', lambda: None)
    csrf = board_csrf_token(client)
    response = client.post('/board', data={
        'csrf_token': csrf, 'action': 'update_instruction_status',
        'instruction_id': 'instruction-1', 'instruction_status': '対応中',
    })
    assert response.status_code == 200
    assert record['instruction_status'] == '対応中'
    assert record['updated_at'] != 'old'

    def fail_save():
        raise OSError('disk error')

    monkeypatch.setattr(app_module, 'save_instructions', fail_save)
    response = client.post('/board', data={
        'csrf_token': csrf, 'action': 'update_instruction_status',
        'instruction_id': 'instruction-1', 'instruction_status': '完了',
    })
    assert response.status_code == 500
    assert record['instruction_status'] == '対応中'


def test_instruction_and_broadcast_posts_persist_to_their_json_sources(tmp_path, monkeypatch):
    instruction_file = tmp_path / 'instructions.json'
    broadcast_file = tmp_path / 'broadcasts.json'
    instruction_file.write_text('[]', encoding='utf-8')
    broadcast_file.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(app_module, 'INSTRUCTIONS_FILE', str(instruction_file))
    monkeypatch.setattr(app_module, 'BROADCASTS_FILE', str(broadcast_file))
    monkeypatch.setattr(app_module, 'instructions', [])
    monkeypatch.setattr(app_module, 'broadcasts', [])
    monkeypatch.setattr(app_module, 'shelters', [])
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    csrf = board_csrf_token(client)

    registered = client.post('/board', data={
        'csrf_token': csrf, 'action': 'register_instruction',
        'district': app_module.AREA_NAME, 'recipient': '住民',
        'priority': '高', 'content': '永続化する指示',
    })
    assert registered.status_code == 200
    stored_instructions = json.loads(instruction_file.read_text(encoding='utf-8'))
    assert stored_instructions[0]['content'] == '永続化する指示'
    assert stored_instructions[0]['instruction_status'] == '未対応'

    preview = client.post('/board', data={
        'csrf_token': csrf, 'action': 'preview_broadcast',
        'broadcast_target': '住民全体', 'title': '永続化する発信',
        'broadcast_content': '発信本文',
    })
    token = re.search(
        r'name="token" value="([^"]+)"',
        preview.get_data(as_text=True)
    ).group(1)
    published = client.post('/board', data={
        'csrf_token': csrf, 'action': 'publish_broadcast', 'token': token,
    })
    assert published.status_code == 200
    stored_broadcasts = json.loads(broadcast_file.read_text(encoding='utf-8'))
    assert stored_broadcasts[0]['title'] == '永続化する発信'


def test_broadcast_confirmation_does_not_save_and_publish_persists_once(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [
        {'id': 1, 'name': '青森小学校', 'district': '中央地区'}
    ])
    monkeypatch.setattr(app_module, 'broadcasts', [])
    monkeypatch.setattr(app_module, 'save_broadcasts', lambda: None)
    csrf = board_csrf_token(client)
    form = {
        'csrf_token': csrf, 'action': 'preview_broadcast',
        'broadcast_target': '避難所利用者',
        'broadcast_shelter_id': '1', 'title': '避難所のお知らせ',
        'broadcast_content': '受付を開始しました',
        'broadcast_simple_content': 'うけつけを はじめました',
    }
    preview = client.post('/board', data=form)
    preview_html = preview.get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', preview_html).group(1)
    assert preview.status_code == 200
    assert '発信内容の確認' in preview_html
    assert app_module.broadcasts == []

    published = client.post('/board', data={
        'csrf_token': csrf, 'action': 'publish_broadcast', 'token': token,
    })
    assert published.status_code == 200
    assert len(app_module.broadcasts) == 1
    assert app_module.broadcasts[0]['shelter'] == '青森小学校'
    assert app_module.broadcasts[0]['simple_content'] == 'うけつけを はじめました'
    duplicate = client.post('/board', data={
        'csrf_token': csrf, 'action': 'publish_broadcast', 'token': token,
    })
    assert duplicate.status_code == 400
    assert len(app_module.broadcasts) == 1


def test_cancel_broadcast_returns_to_form_with_values_preserved(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'shelters', [])
    monkeypatch.setattr(app_module, 'broadcasts', [])
    csrf = board_csrf_token(client)
    preview = client.post('/board', data={
        'csrf_token': csrf, 'action': 'preview_broadcast',
        'broadcast_target': '住民全体', 'title': '保持する題名',
        'broadcast_content': '保持する内容',
    })
    token = re.search(
        r'name="token" value="([^"]+)"',
        preview.get_data(as_text=True)
    ).group(1)

    correction = client.post('/board', data={
        'csrf_token': csrf, 'action': 'cancel_broadcast', 'token': token,
    })
    html = correction.get_data(as_text=True)
    assert correction.status_code == 200
    assert 'value="保持する題名"' in html
    assert '保持する内容</textarea>' in html
    assert app_module.broadcasts == []


def test_home_api_and_view_share_only_resident_instructions_and_latest_broadcasts(tmp_path, monkeypatch):
    instruction_file = tmp_path / 'instructions.json'
    broadcast_file = tmp_path / 'broadcasts.json'
    saved_instructions = [
        {
            'id': f'i-{index}', 'target': target, 'district': '中央地区',
            'content': f'指示{index}', 'simple_content': '簡単な指示',
            'priority': '高', 'instruction_status': '未対応',
            'created_at': f'2026-10-04T05:{index:02}:00+09:00',
        }
        for index, target in enumerate(['住民'] * 6 + ['職員'])
    ]
    saved_broadcasts = [
        {
            'id': f'b-{index}', 'target': '住民全体', 'title': f'発信{index}',
            'content': 'お知らせ', 'simple_content': '',
            'published_at': f'2026-10-04T05:{index:02}:00+09:00',
        }
        for index in range(7)
    ]
    instruction_file.write_text(json.dumps(saved_instructions, ensure_ascii=False), encoding='utf-8')
    broadcast_file.write_text(json.dumps(saved_broadcasts, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app_module, 'INSTRUCTIONS_FILE', str(instruction_file))
    monkeypatch.setattr(app_module, 'BROADCASTS_FILE', str(broadcast_file))
    monkeypatch.setattr(app_module, 'instructions', saved_instructions)
    monkeypatch.setattr(app_module, 'broadcasts', saved_broadcasts)
    monkeypatch.setattr(app_module, 'shelters', [])

    client = app_module.app.test_client()
    response = client.get('/api/disaster_info')
    data = response.get_json()
    assert response.status_code == 200
    assert len(data['instructions']) == 6
    assert all(item['display_recipient'] == '住民' for item in data['instructions'])
    assert len(data['broadcasts']) == 5
    home = client.get('/').get_data(as_text=True)
    assert home.count('class="broadcast-home-item emergency-content"') == 5
    assert '職員</span>' not in home
    assert 'やさしい日本語' in home
    assert 'aria-pressed="true"' in home
    assert '10分おき自動更新' in home


def test_weather_api_no_warning_and_failure_are_not_conflated(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, 'get_weather_warnings', lambda: {
        'area_name': '青森市',
        'warnings': [],
        'last_fetch_time': '2026年10月04日 14:00',
        'report_time': '2026年10月04日 13:50',
    })
    no_warnings = client.get('/api/weather_warnings').get_json()
    assert no_warnings['warnings'] == []
    assert no_warnings['area_name'] == '青森市'

    monkeypatch.setattr(app_module, 'get_weather_warnings', lambda: {
        'area_name': '青森市',
        'warnings': [],
        'last_fetch_time': '2026年10月04日 14:10',
        'report_time': '取得失敗',
        'error': True,
    })
    failed = client.get('/api/weather_warnings').get_json()
    assert failed['error'] is True
    html = client.get('/').get_data(as_text=True)
    assert '発表なしを意味するものではありません' in html
    assert '気象警報・注意報を取得できませんでした' in html


def test_board_csrf_and_broadcast_invalid_values_are_rejected(monkeypatch):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True
    monkeypatch.setattr(app_module, 'broadcasts', [])
    csrf = board_csrf_token(client)
    no_csrf = client.post('/board', data={
        'action': 'preview_broadcast', 'broadcast_target': '住民全体',
        'title': 'タイトル', 'broadcast_content': '内容',
    })
    assert no_csrf.status_code == 400
    invalid = client.post('/board', data={
        'csrf_token': csrf, 'action': 'preview_broadcast',
        'broadcast_target': '不正', 'title': '', 'broadcast_content': '',
    })
    assert invalid.status_code == 400
    assert app_module.broadcasts == []


def test_parse_area_warnings_matches_aomori_city_area_codes():
    warning_data = [{
        'reportDatetime': '2026-09-04T16:07:00+09:00',
        'warning': {
            'class20Items': [
                {'areaCode': '0220100', 'kinds': [{'code': '10', 'status': '発表'}]},
                {'areaCode': '0220500', 'kinds': [{'status': '発表警報・注意報はなし'}]},
            ]
        }
    }]

    warnings, report_datetime = app_module.parse_area_warnings(warning_data)

    assert report_datetime == '2026-09-04T16:07:00+09:00'
    assert len(warnings) == 1
    assert warnings[0]['code'] == '10'
    assert warnings[0]['name'] == app_module.WARNING_CODES['10']


def test_parse_area_warnings_includes_city_area_when_ward_area_is_also_present():
    warnings, _ = app_module.parse_area_warnings([{
        'warning': {
            'class10Items': [
                {'areaCode': '020010', 'kinds': []},
            ],
            'class20Items': [
                {'areaCode': '0220100', 'kinds': [
                    {'code': '05', 'status': '継続'},
                    {'code': '00', 'status': '解除'},
                ]},
            ],
        },
    }])

    assert [warning['code'] for warning in warnings] == ['05']
