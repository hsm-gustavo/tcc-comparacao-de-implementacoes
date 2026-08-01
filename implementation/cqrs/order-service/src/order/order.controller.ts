import { Controller, Post, Body, Get, Param } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { CreateOrderDto } from './dto/create-order.dto';
import { OrderCommandService } from './order.command.service';
import { OrderQueryService } from './order.query.service';

@ApiTags('orders')
@Controller('orders')
export class OrderController {
  constructor(
    private readonly commandService: OrderCommandService,
    private readonly queryService: OrderQueryService,
  ) {}

  @Post()
  @ApiOperation({ summary: 'Cria um pedido (Gera Comando/Evento de Escrita)' })
  @ApiBody({ type: CreateOrderDto })
  async placeOrder(@Body() body: CreateOrderDto) {
    return this.commandService.createOrder(body);
  }

  @Get(':id')
  @ApiOperation({
    summary: 'Busca detalhes do pedido direto da Projeção de Leitura',
  })
  @ApiParam({ name: 'id', type: String })
  async findOne(@Param('id') id: string) {
    return this.queryService.getOrderById(id);
  }

  @Post('replay')
  @ApiOperation({
    summary: 'Reprocessa todos os eventos para reconstruir o estado',
  })
  async triggerReplay() {
    return this.commandService.replayEvents();
  }
}
