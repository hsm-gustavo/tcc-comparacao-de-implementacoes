import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { BillingService } from './billing.service';
import { RefundPaymentDto } from './dto/refund-payment.dto';
import { ProcessPaymentDto } from './dto/process-payment.dto';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(private readonly billingService: BillingService) {}

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

  @Post('try')
  @ApiOperation({ summary: 'TCC Try: tenta criar pagamento' })
  @ApiBody({ type: ProcessPaymentDto })
  try(@Body() dto: ProcessPaymentDto) {
    return this.billingService.preparePayment(dto);
  }

  @Post('confirm')
  @ApiOperation({ summary: 'TCC Confirm: confirma pagamento' })
  @ApiBody({ type: RefundPaymentDto })
  confirm(@Body() dto: RefundPaymentDto) {
    return this.billingService.commitPayment(dto.orderId);
  }

  @Post('cancel')
  @ApiOperation({ summary: 'TCC Cancel: cancela pagamento' })
  @ApiBody({ type: RefundPaymentDto })
  cancel(@Body() dto: RefundPaymentDto) {
    return this.billingService.rollbackPayment(dto.orderId);
  }
}
