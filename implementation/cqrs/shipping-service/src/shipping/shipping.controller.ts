import { Controller, Get, Param } from '@nestjs/common';
import { ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { ShippingQueryService } from './shipping.query.service';

@ApiTags('shipping')
@Controller('shipping')
export class ShippingController {
  constructor(private readonly queryService: ShippingQueryService) {}

  @Get('tracking/:orderId')
  @ApiOperation({ summary: 'Consulta o tracking direto do banco de leitura' })
  @ApiParam({ name: 'orderId', type: String })
  async getTracking(@Param('orderId') orderId: string) {
    return this.queryService.getShipmentByOrderId(orderId);
  }
}
