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

  @Post('prepare')
  @ApiOperation({ summary: 'Internal API: Prepare shipment for 2PC' })
  prepare(@Body() dto: CreateShipmentDto) {
    return this.shippingService.prepareShipment(dto);
  }

  @Post('commit')
  @ApiOperation({ summary: 'Internal API: Commit shipment for 2PC' })
  commit(@Body() dto: CreateShipmentDto) {
    return this.shippingService.commitShipment(dto.orderId);
  }

  @Post('rollback')
  @ApiOperation({ summary: 'Internal API: Rollback shipment for 2PC' })
  rollback(@Body() dto: CreateShipmentDto) {
    return this.shippingService.rollbackShipment(dto.orderId);
  }

  @Get('tracking/:orderId')
  @ApiOperation({ summary: 'Consulta tracking por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  getTracking(@Param('orderId') orderId: string) {
    return this.shippingService.getShipmentByOrderId(orderId);
  }
}
