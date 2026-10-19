# Эквивалентная сага оплаты на обычном Python (тот же смысл, ручная механика).
import json


class SagaError(Exception):
    pass


class OrderSaga:
    def __init__(self, stock, bank, post, mail):
        self.stock, self.bank, self.post, self.mail = stock, bank, post, mail
        self.compensations = []

    def compensate(self):
        done = 0
        for fn, args in reversed(self.compensations):
            try:
                fn(*args)
                done += 1
            except Exception:
                pass
        return done

    def run(self, order_id, amount):
        try:
            reservation = self.stock.reserve(order_id)
            self.compensations.append((self.stock.release, (order_id,)))

            payment = self.bank.charge(order_id, amount)
            self.compensations.append((self.bank.refund, (payment["tx"],)))

            if not payment["ok"]:
                raise SagaError("банк отклонил платёж")

            shipment = self.post.book(order_id)
            self.compensations.append((self.post.cancel, (shipment["track"],)))

            self.mail.send(order_id, shipment["track"])
            return {"order": order_id, "tx": payment["tx"], "track": shipment["track"]}
        except Exception as exc:
            compensated = self.compensate()
            raise SagaError(f"выполнение сорвалось, компенсаций выполнено: {compensated}; причина: {exc}") from exc
