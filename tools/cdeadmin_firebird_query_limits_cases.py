"""Native fetch-bound cases for the owned Firebird query qualification DB."""


def qualify_fetch_limits(client, handle, run_request, result):
    """Run independent cases; caller owns the isolated DB and its cleanup."""
    def sql(source, policy=None, asynchronous=False):
        return run_request({'source': source, 'parameters': [],
                            'output_policy': policy or {}}, asynchronous)

    sql('CREATE TABLE OWNED_FETCH_LIMITS (N INTEGER PRIMARY KEY, V INTEGER)')
    client.control_transaction(handle, 'commit')
    sql('EXECUTE BLOCK AS DECLARE I INTEGER = 0; BEGIN '
        'WHILE (I < 100) DO BEGIN '
        'INSERT INTO OWNED_FETCH_LIMITS VALUES (:I, 0); '
        'I = I + 1; END END')
    client.control_transaction(handle, 'commit')
    for asynchronous in (False, True):
        for limit, predicate, expected in (
                (1, 'N < 0', 0), (7, 'N < 3', 3), (7, 'N < 7', 7),
                (7, 'N >= 0', 7), (100, 'N >= 0', 100),
                (101, 'N >= 0', 100), (None, 'N >= 0', 100)):
            case = f'fetch-limit-{asynchronous}-{limit}-{expected}-{predicate}'
            try:
                response = sql('SELECT N FROM OWNED_FETCH_LIMITS WHERE ' +
                               predicate + ' ORDER BY N', {'max_rows': limit},
                               asynchronous)
                payload = response['payload']
                assert payload['rows'] == [(n,) for n in range(expected)]
                observation = payload.get('fetch_observation')
                if limit is None:
                    assert observation is None
                else:
                    assert observation['rows_returned'] == expected
                    assert observation['limit_reached'] is (expected == limit)
                    assert observation['end_of_cursor_observed'] is (
                        expected < limit)
                    assert observation['total_rows'] == (
                        expected if expected < limit else None)
                assert handle.main_transaction.is_active()
                result['cases'].append(case)
            except Exception as exc:
                result['failures'].append({'case': case,
                                          'error_type': type(exc).__name__})
            finally:
                client.control_transaction(handle, 'rollback')
        case = f'fetch-limit-returning-full-mutation-rollback-{asynchronous}'
        try:
            response = sql('UPDATE OWNED_FETCH_LIMITS SET V = 1 RETURNING N',
                           {'max_rows': 7}, asynchronous)
            assert len(response['payload']['rows']) == 7
            assert sql('SELECT COUNT(*) FROM OWNED_FETCH_LIMITS WHERE V = 1')[
                'payload']['rows'] == [(100,)]
            assert handle.main_transaction.is_active()
            client.control_transaction(handle, 'rollback')
            assert sql('SELECT COUNT(*) FROM OWNED_FETCH_LIMITS WHERE V = 1')[
                'payload']['rows'] == [(0,)]
            result['cases'].append(case)
        except Exception as exc:
            result['failures'].append({'case': case,
                                      'error_type': type(exc).__name__})
        finally:
            client.control_transaction(handle, 'rollback')
