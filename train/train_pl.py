import sys
sys.path.append('../')
from data.get_data import get_data
from data.get_data_pl import DataModule
from model.get_model import get_model
from lense.get_lense import get_lense
from lense.lenseA import LenseA
import torch
import transformer_lens.utils as utils
import torch.nn.functional as F
import pytorch_lightning as pl
from tqdm import tqdm
import math
import argparse


#TODO (MS): clean up args and keep only relavent ones
#### SET UP USER ARGS
parser = argparse.ArgumentParser()
parser.add_argument("--local_rank", type=int)
parser.add_argument("--device", type=int)
parser.add_argument("--lr", default=5e-5, type=float)
parser.add_argument("--temp", default=3, type=float)
parser.add_argument("--alpha", default=0.5, type=float)
parser.add_argument("--epochs", default=3, type=int)
parser.add_argument("--warmup_steps", default=10000, type=int)
parser.add_argument("--batch_size", default=2, type=int)
parser.add_argument("--resume_step", default=0, type=int)
parser.add_argument("--num_steps_per_checkpoint", default=5, type=int)
parser.add_argument("--checkpoint_dir", default="/grand/projects/SuperBERT/mansisak/kd_ckpts/", type=str)
parser.add_argument("--layer_number", default=0, type=int)
args = parser.parse_args()

#### Pytorch lighting

class LightningLens(pl.LightningModule):

  def __init__(self):
    super().__init__()

    #self.model=model
    #print("init step device: ", self.device)
    self.base_model = get_model(device=self.device)
    self.hook_name = 'result'
    self.n_layer = 0
    self.hook_id = utils.get_act_name(self.hook_name, self.n_layer)

    #Initalize lense with model unembed/bias matrix
    lens_param = {'unembed': self.base_model.W_U, 'bias': self.base_model.b_U, 'n_head':self.base_model.cfg.n_heads, 'd_model': self.base_model.cfg.d_model, 'd_vocab': self.base_model.cfg.d_vocab, 'lense_class': LenseA}

    #making lense
    self.attn_lens = get_lense(n_layers=1, **lens_param)# .to(device)
   
  def setup(self, stage):
    #print("setup step device: ", self.device)
    #print("setup step work around device: ", self.trainer.strategy.root_device)
    self.model = get_model(device=self.trainer.strategy.root_device)
    return
    

  def forward(self, cache):
      #print("forward step device: ", self.device)
        
      inputs = []
      inputs.append(cache[self.hook_id])
      input_tensor = torch.stack(inputs)

      attn_lens_out = self.attn_lens(input_tensor)
      lens_out = attn_lens_out[0]

      return lens_out
      '''
      batch_size, channels, width, height = x.size()
      # (b, 1, 28, 28) -> (b, 1*28*28)
      x = x.view(batch_size, -1)

      # layer 1 (b, 1*28*28) -> (b, 128)
      x = self.layer_1(x)
      x = torch.relu(x)

      # layer 2 (b, 128) -> (b, 256)
      x = self.layer_2(x)
      x = torch.relu(x)

      # layer 3 (b, 256) -> (b, 10)
      x = self.layer_3(x)

      # probability distribution over labels
      x = torch.log_softmax(x, dim=1)

      return x
      '''

  def kl_loss(self, logits, lens_logits):
    kldiv = torch.nn.KLDivLoss(reduction='batchmean')
    k_logits, k_lens_out = F.log_softmax(logits, dim=-1), F.log_softmax(lens_logits, dim=-1)

    loss = kldiv(k_lens_out, k_logits)
    return loss

  '''
  def on_before_optimizer_step(self, optimizer) -> None:
    print("on_before_opt enter")
    for p in self.trainable_params:
        if p.grad is None:
            print(p)
    print("on_before_opt exit")
  '''

  def training_step(self, train_batch, batch_idx):
      #x, y = train_batch
      #print("train step device: ", self.device)
      #self.model = get_model(device=self.device)
      prompt = train_batch['text']
      tokens = self.model.to_tokens(prompt)
      #print('device: ', self.device)
      #print('Tokens device number: ', tokens.get_device())
      #print('LLM device number: ', self.model.device)

      #self.model = get_model(device=self.device)

      with torch.no_grad():
          logits, cache = self.model.run_with_cache(tokens, remove_batch_dim=False)
      #print("computed grads")
      lens_logits = self.forward(cache)
      loss = self.kl_loss(logits, lens_logits)
      self.log('train_loss', loss)
      return loss


  '''
  def validation_step(self, val_batch, batch_idx):
      x, y = val_batch
      logits = self.forward(x)
      loss = self.cross_entropy_loss(logits, y)
      self.log('val_loss', loss)
  '''

  def configure_optimizers(self):
    optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)
    return optimizer
  
  #TODO(MS): register an early stopping call back which quits training if the loss/some metric drops below a certain pont
  #TODO(MS): when training quits, save a copy of the appropriately named lense
  #TODO(MS): test and make sure distributed training works accross nodes

#train
#LLM = get_model()
model = LightningLens()
data_module = DataModule()
trainer = pl.Trainer(strategy='ddp_find_unused_parameters_true',
                     max_epochs=1,)
                     #TODO(MS): eventually use the profile to find bottlenecks: profiler='simple')

trainer.fit(model, data_module)
#TODO (MS): implement checkpointing
'''
#single device
device = "cuda:0" if torch.cuda.is_available() else "cpu"
pin_memory=False
if device!="cpu":
    pin_memory=True

batch_size = args.batch_size
dataloader = get_data(streaming=True, 
                        dataset_name="c4",
                        batch_size=batch_size,
                        pin_memory=pin_memory,
                        device=device,
                        num_workers=16)


# do the training one layer at a time to prevent ram from running out
hook_name = 'result'
kldiv = torch.nn.KLDivLoss(reduction='batchmean')

n_layer = args.layer_number

#Initalize lense with model unembed/bias matrix
lens_param = {'unembed': model.W_U, 'bias': model.b_U, 'n_head': model.cfg.n_heads, 'd_model': model.cfg.d_model, 'd_vocab': model.cfg.d_vocab, 'lense_class': LenseA}
attn_lens = get_lense(n_layers=1, **lens_param).to(device)
hook_id = utils.get_act_name(hook_name, n_layer)
total_steps = 5
progress_bar = tqdm(range(total_steps))

for i, data in enumerate(dataloader):
  if i == total_steps:
      break

  prompt = data['text']
  tokens = model.to_tokens(prompt)

  with torch.no_grad():
      logits, cache = model.run_with_cache(tokens, remove_batch_dim=False)
    
  inputs = []
  inputs.append(cache[hook_id])
  input_tensor = torch.stack(inputs)

  attn_lens_out = attn_lens(input_tensor)
  lens_out = attn_lens_out[0]

  #TODO (MS): are we supposed to log softmax both, or just one of these quantities
  k_logits, k_lens_out = F.log_softmax(logits, dim=-1), F.log_softmax(lens_out, dim=-1)

  loss = kldiv(k_lens_out, k_logits)
  loss.backward()
    
  #update tqdm bar
  progress_bar.update(1)
    
#Save attn lens in correct location
name = "attn_lens_layer_"+str(n_layer)
torch.save(attn_lens, name)
#TODO (MS): need to test that lense is useable for model analysis later
'''
