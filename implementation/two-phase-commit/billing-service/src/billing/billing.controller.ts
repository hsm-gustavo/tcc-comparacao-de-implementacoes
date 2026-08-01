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

  @Post('prepare')
  @ApiOperation({ summary: 'Internal API: Prepare payment for 2PC' })
  @ApiBody({ type: ProcessPaymentDto })
  prepare(@Body() dto: ProcessPaymentDto) {
    return this.billingService.preparePayment(dto);
  }

  @Post('commit')
  @ApiOperation({ summary: 'Internal API: Commit payment for 2PC' })
  @ApiBody({ type: RefundPaymentDto })
  commit(@Body() dto: RefundPaymentDto) {
    return this.billingService.commitPayment(dto.orderId);
  }

  @Post('rollback')
  @ApiOperation({ summary: 'Internal API: Rollback payment for 2PC' })
  @ApiBody({ type: RefundPaymentDto })
  rollback(@Body() dto: RefundPaymentDto) {
    return this.billingService.rollbackPayment(dto.orderId);
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
}
