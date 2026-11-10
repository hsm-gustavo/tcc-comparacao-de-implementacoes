import { MessageRecovery } from '../message-recovery';
import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { BillingService } from './billing.service';
import { RefundPaymentDto } from './dto/refund-payment.dto';
import { ProcessPaymentDto } from './dto/process-payment.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(
    private readonly billingService: BillingService,
    private readonly recovery: MessageRecovery,
  ) {}

  @Post('process')
  @ApiOperation({ summary: 'Internal API: Process payment' })
  process(@Body() dto: ProcessPaymentDto) {
    return this.billingService.processPayment(dto);
  }

  @Get('payments/:orderId')
  @ApiOperation({ summary: 'Busca pagamento por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  findOne(@Param('orderId') orderId: string) {
    return this.billingService.findOne(orderId);
  }

  @Post('refund')
  @ApiOperation({ summary: 'Solicita estorno de pagamento' })
  @ApiBody({ type: RefundPaymentDto })
  async refund(@Body() dto: RefundPaymentDto) {
    return this.billingService.refundPayment(dto.orderId);
  }

  @EventPattern('warehouse.stock_reserved')
  async handleStockReserved(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.billingService.onStockReserved(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'billing-saga-queue');
    }
  }

  @EventPattern('shipping.shipment_failed')
  async handleShippingFailed(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.billingService.onShippingFailed(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'billing-saga-queue');
    }
  }
}
