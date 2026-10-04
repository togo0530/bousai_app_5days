import app as app_module


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

    response = client.post('/shelter_register', data={'name': '新しい避難所'})

    assert response.status_code == 200
    assert '避難所を登録しました' in response.get_data(as_text=True)
    assert any(shelter['name'] == '新しい避難所' for shelter in app_module.shelters)


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
