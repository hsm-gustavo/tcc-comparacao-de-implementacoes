import { BadRequestException } from '@nestjs/common';
import { BillingService } from './billing.service';
import { PaymentStatus } from './entities/payment.entity';

describe('Billing event recovery', () => {
  const payload = { orderId: 'one', amount: 10 };
  function fixture() {
    const service = new BillingService({} as any);
    const payment = jest.spyOn(service, 'processSagaPayment');
    const emit = jest
      .spyOn(service as any, 'emit')
      .mockResolvedValue(undefined);
    return { service, payment, emit };
  }
  it('does not compensate a technical error', async () => {
    const f = fixture();
    f.payment.mockRejectedValue(new Error('database down'));
    await expect(f.service.onStockReserved(payload)).rejects.toThrow(
      'database down',
    );
    expect(f.emit).not.toHaveBeenCalled();
  });
  it('does not compensate a failed publication after completing payment', async () => {
    const f = fixture();
    f.payment.mockResolvedValue({ status: PaymentStatus.COMPLETED } as any);
    f.emit.mockRejectedValue(new Error('broker failed'));
    await expect(f.service.onStockReserved(payload)).rejects.toThrow(
      'broker failed',
    );
    expect(f.emit).toHaveBeenCalledTimes(1);
    expect(f.emit.mock.calls[0][1]).toBe('billing.payment_confirmed');
  });
  it('does not forward a refunded payment', async () => {
    const f = fixture();
    f.payment.mockResolvedValue({ status: PaymentStatus.REFUNDED } as any);
    await f.service.onStockReserved(payload);
    expect(f.emit).not.toHaveBeenCalled();
  });
  it('ACKs a business rejection only after both compensation events are published', async () => {
    const f = fixture();
    f.payment.mockRejectedValue(new BadRequestException('invalid amount'));
    await f.service.onStockReserved(payload);
    expect(f.emit).toHaveBeenCalledTimes(2);
    expect(
      f.emit.mock.calls.every((call) => call[1] === 'billing.payment_failed'),
    ).toBe(true);
  });
});
