import { Controller, Get, Param } from '@nestjs/common';
import { ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { ShippingService } from './shipping.service';

@ApiTags('shipping')
@Controller('shipping')
export class ShippingController {
  constructor(private readonly shippingService: ShippingService) {}

  @Get('tracking/:orderId')
  @ApiOperation({ summary: 'Consulta tracking por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  getTracking(@Param('orderId') orderId: string) {
    return this.shippingService.getShipmentByOrderId(orderId);
  }
}
