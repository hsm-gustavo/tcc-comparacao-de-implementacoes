import {
  Body,
  Controller,
  Get,
  Param,
  Post,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { BillingCommandService } from './billing.command.service';
import { BillingQueryService } from './billing.query.service';
import { RefundPaymentDto } from './dto/refund-payment.dto';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(
    private readonly commandService: BillingCommandService,
    private readonly queryService: BillingQueryService,
  ) {}

  @Get('payments/:orderId')
  @ApiOperation({
    summary: 'Busca faturamento direto da projeção (Sem custo de reducers)',
  })
  @ApiParam({ name: 'orderId', type: String })
  findOne(@Param('orderId') orderId: string) {
    return this.queryService.getPaymentByOrderId(orderId);
  }

  @Post('refund')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Solicita estorno (Gera Comando modificador de Escrita)',
  })
  @ApiBody({ type: RefundPaymentDto })
  async refund(@Body() dto: RefundPaymentDto) {
    await this.commandService.refundPayment(dto.orderId, 0);
    return { orderId: dto.orderId, message: 'Refund event recorded.' };
  }
}
