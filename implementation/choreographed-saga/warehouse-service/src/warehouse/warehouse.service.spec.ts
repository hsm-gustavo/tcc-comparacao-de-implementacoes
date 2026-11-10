import { BadRequestException } from '@nestjs/common';
import { WarehouseService } from './warehouse.service';
import { StockReservationStatus } from './entities/stock-reservation.entity';

describe('Warehouse event replay', () => {
  const payload = {
    orderId: 'one',
    productId: 'product',
    quantity: 1,
    amount: 10,
  };
  function fixture() {
    const service = new WarehouseService({} as any, {} as any);
    const reserve = jest.spyOn(service, 'reserveForSaga');
    const emit = jest
      .spyOn(service as any, 'emit')
      .mockResolvedValue(undefined);
    return { service, reserve, emit };
  }
  it('replays stock_reserved after publication failed without requiring a new reservation', async () => {
    const f = fixture();
    f.reserve.mockResolvedValue({
      reservation: { status: StockReservationStatus.RESERVED } as any,
      created: false,
    });
    f.emit.mockRejectedValueOnce(new Error('publish failed'));
    await expect(f.service.onOrderCreated(payload)).rejects.toThrow(
      'publish failed',
    );
    await f.service.onOrderCreated(payload);
    expect(f.emit.mock.calls.map((call) => call[1])).toEqual([
      'warehouse.stock_reserved',
      'warehouse.stock_reserved',
    ]);
  });
  it('does not restart a compensated reservation', async () => {
    const f = fixture();
    f.reserve.mockResolvedValue({
      reservation: { status: StockReservationStatus.RELEASED } as any,
      created: false,
    });
    await f.service.onOrderCreated(payload);
    expect(f.emit).not.toHaveBeenCalled();
  });
  it('propagates technical failures without emitting a business rejection', async () => {
    const f = fixture();
    f.reserve.mockRejectedValue(new Error('database down'));
    await expect(f.service.onOrderCreated(payload)).rejects.toThrow(
      'database down',
    );
    expect(f.emit).not.toHaveBeenCalled();
  });
  it('finishes a business rejection after its failure event is confirmed', async () => {
    const f = fixture();
    f.reserve.mockRejectedValue(new BadRequestException('Insufficient stock'));
    await f.service.onOrderCreated(payload);
    expect(f.emit).toHaveBeenCalledWith(
      'order-saga-queue',
      'warehouse.stock_failed',
      expect.anything(),
    );
  });
});
