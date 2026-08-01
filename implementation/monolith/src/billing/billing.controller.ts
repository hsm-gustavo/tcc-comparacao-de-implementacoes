import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { BillingService } from './billing.service';
import { RefundPaymentDto } from './dto/refund-payment.dto';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(private readonly billingService: BillingService) {}

  @Get('payments/:orderId')
  @ApiOperation({ summary: 'Busca pagamento por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  findOne(@Param('orderId') orderId: string) {
    return this.billingService.findOne(orderId);
  }

  @Post('refund')
  @ApiOperation({ summary: 'Solicita estorno de pagamento' })
  @ApiBody({ type: RefundPaymentDto })
  async refund(@Body() body: RefundPaymentDto) {
    return this.billingService.refundPayment(body.orderId);
  }
}
