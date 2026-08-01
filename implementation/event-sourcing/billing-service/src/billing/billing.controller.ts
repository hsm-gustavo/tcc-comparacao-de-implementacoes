import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Post,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { BillingService } from './billing.service';
import { RefundPaymentDto } from './dto/refund-payment.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(private readonly billingService: BillingService) {}

  @Get('payments/:orderId')
  @ApiOperation({
    summary: 'Busca o status do faturamento reconstruindo os eventos da ordem',
  })
  @ApiParam({ name: 'orderId', type: String })
  async findOne(@Param('orderId') orderId: string) {
    return this.billingService.findOne(orderId);
  }

  @Post('refund')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Solicita estorno de pagamento (Registra o evento de estorno)',
  })
  @ApiBody({ type: RefundPaymentDto })
  async refund(@Body() dto: RefundPaymentDto) {
    return this.billingService.refundPayment(dto.orderId);
  }

  @EventPattern('warehouse.stock_reserved')
  async handleStockReserved(
    @Payload()
    payload: {
      orderId: string;
      productId: string;
      quantity: number;
      totalAmount: number;
    },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.billingService.processPaymentFromEvent(
        payload.orderId,
        payload.totalAmount,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }

  @Post('replay')
  @ApiOperation({
    summary:
      'Executa o replay dos eventos de faturamento aplicando Upsert na tabela de payments',
  })
  async triggerReplay() {
    return this.billingService.replayEvents();
  }
}
