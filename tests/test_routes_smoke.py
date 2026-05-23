from tests.test_support import AppTestCase


class RouteSmokeTests(AppTestCase):
    def test_withdraw_page_loads_after_login(self):
        self.create_user(username='smoke_user', password='abcdef', coins=3000)
        self.login(username='smoke_user', password='abcdef')
        response = self.client.get('/work/withdraw')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'My Withdrawal Requests', response.data)

    def test_game_page_loads_after_login(self):
        self.create_user(username='gamer', password='abcdef', coins=5000)
        self.login(username='gamer', password='abcdef')
        response = self.client.get('/game/emperors-circle')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Emperor', response.data)
