import { Controller, Post, Body } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiTags } from '@nestjs/swagger';
import { OrderService } from './order.service';
import { CreateOrderDto } from './dto/create-order.dto';

@ApiTags('orders')
@Controller('orders')
export class OrderController {
  constructor(private readonly orderService: OrderService) {}

  @Post()
  @ApiOperation({ summary: 'Cria um pedido' })
  @ApiBody({ type: CreateOrderDto })
  async placeOrder(@Body() body: CreateOrderDto) {
    return this.orderService.createOrder(body);
  }
}
