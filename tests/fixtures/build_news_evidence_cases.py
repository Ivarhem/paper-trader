"""Emit 20 synthetic regression cases. No real market observations or human labels."""
import json


def make_cases():
    cases = []
    themes = ['반도체', '전력', '조선', '방산', '원전', '바이오', '자동차', '로봇', '게임', '엔터',
              '철강', '금융', '보험', '건설', '정유', '화학', '은행', '배터리', '통신', '유통']
    for i, theme in enumerate(themes, 1):
        prefix = f'synthetic-{i:02}'
        def doc(suffix, text, stance='support', **kw):
            return {'source_id': prefix + '-' + suffix,
                    'url': f'https://example.org/{prefix}/{suffix}',
                    'title': f'{theme} 수요 전망 자료',
                    'published_at': '2026-09-01T08:00:00+09:00',
                    'available_at': '2026-09-01T08:10:00+09:00',
                    'stance': stance, 'text': text, **kw}
        quote = f'{theme} 수요 전망은 검증되지 않은 합성 시나리오다.'
        counter = f'{theme} 수요 증가는 아직 확인되지 않았다는 반론이 있다.'
        correction = f'{theme} 수요 증가율은 20%가 아니라 2%로 정정되었다.'
        filler = '이 문장은 추출 시험용 배경 문구이며 실제 시장의 관측이나 분석이 아니다.\n'
        cases.append({'case_id': prefix, 'dataset_kind': 'synthetic',
                      'cutoff': '2026-09-01T23:59:00+09:00',
                      'claims': [{'claim_id': 'demand', 'text': f'{theme} 수요 전망을 검토한다.',
                                  'keywords': [theme, '수요']}],
                      'documents': [doc('original', quote + '\n' + filler * 300),
                                    doc('counter', counter, 'counterargument'),
                                    doc('correction', correction, 'correction'),
                                    doc('future', '사건 이후에 작성된 문장이다.',
                                        published_at='2026-09-02T08:00:00+09:00',
                                        available_at='2026-09-02T08:10:00+09:00'),
                                    doc('unknown', '발표일이 없는 참고자료다.', published_at=None)],
                      'annotation': {'origin': 'synthetic', 'reviewer': 'fixture-generator'},
                      'required_evidence': [{'source_id': prefix + '-original', 'quote': quote, 'kind': 'support'},
                                            {'source_id': prefix + '-counter', 'quote': counter, 'kind': 'counterargument'},
                                            {'source_id': prefix + '-correction', 'quote': correction, 'kind': 'correction'}]})
    return cases


if __name__ == '__main__':
    print(json.dumps(make_cases(), ensure_ascii=False))
