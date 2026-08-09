import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { ShippingService } from './shipping.service';
import { CreateShipmentDto } from './dto/create-shipment.dto';

@ApiTags('shipping')
@Controller('shipping')
export class ShippingController {
  constructor(private readonly shippingService: ShippingService) {}

  @Post()
  @ApiOperation({ summary: 'Internal API: Create shipment' })
  create(@Body() dto: CreateShipmentDto) {
    return this.shippingService.createShipment(dto);
  }

  @Post('cancel')
  @ApiOperation({ summary: 'Internal API: cancel shipment' })
  cancel(@Body() dto: CreateShipmentDto) {
    return this.shippingService.cancelShipment(dto.orderId);
  }

  @Get('tracking/:orderId')
  @ApiOperation({ summary: 'Consulta tracking por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  getTracking(@Param('orderId') orderId: string) {
    return this.shippingService.getShipmentByOrderId(orderId);
  }
}
