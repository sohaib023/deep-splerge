import os

import torch
import torchvision
import torch.nn.functional as F
from torchvision import transforms
from torch.utils.data import DataLoader

from transforms import get_transform
from dataloader import TableDataset

class SFCN(torch.nn.Module):
    
    #Our batch shape for input x is (3,?,?)
    def __init__(self):
        super(SFCN, self).__init__()
        
        #Input channels = 3, output channels = 18
        self.conv1 = torch.nn.Conv2d(3, 18, kernel_size=7, stride=1, padding=3)

        self.conv2 = torch.nn.Conv2d(18, 18, kernel_size=7, stride=1, padding=3)

        self.dil_conv3 = torch.nn.Conv2d(18, 18, kernel_size=7, dilation=2, stride=1, padding=6)
        
    def forward(self, x):
        
        #Computes the activation of the first convolution
        #Size changes from (3,?,?) to (18,?,?)
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.dil_conv3(x))

        return(x)

class Splerge(torch.nn.Module):
    
    #Our batch shape for input x is (3,256,256)
    def __init__(self):
        super(Splerge, self).__init__()
        self.blocks = 5
        self.block_inputs = [18, 55, 55, 55, 55]
        self.block_conv1x1_output = 36
        self.block_output = 55

        # get shared FCN
        self.sfcn = SFCN()

        #Input channels = 3, output channels = 18
        # self.dil_conv2 = torch.nn.Conv2d(18, 6, kernel_size=5, dilation=2, stride=1, padding=4)
        # self.dil_conv3 = torch.nn.Conv2d(18, 6, kernel_size=5, dilation=3, stride=1, padding=6)
        # self.dil_conv4 = torch.nn.Conv2d(18, 6, kernel_size=5, dilation=4, stride=1, padding=8)

        # 1x2 max pooling for rows
        self.row_pool = torch.nn.MaxPool2d(kernel_size=(1,2), stride=(1,2))
        # 2x1 max pooling for columns
        self.col_pool = torch.nn.MaxPool2d(kernel_size=(2,1), stride=(2,1))

        # 1x1 convolution for top branch
        self.conv4_1x1_top = torch.nn.Conv2d(18, self.block_conv1x1_output, kernel_size=1) 

        # 1x1 convolution for bottom branch
        self.conv4_1x1_bottom = torch.nn.Conv2d(18, 1, kernel_size=1) 

    # dilated convolutions specifically for this RPN
    def dil_conv2d(self, input_feature, in_size, out_size=6, kernel_size=5, dilation=1, stride=1, padding=1):
        conv_layer = torch.nn.Conv2d(in_size, out_size, kernel_size=kernel_size, dilation=dilation, stride=stride, padding=padding).to(device)
        return conv_layer(input_feature)

    def rpn_block(self, input_feature, block_num):
        height, width = input_feature.shape[-2:]

        # dilated convolutions 2/3/4
        x1 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=2, padding=4))
        x2 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=3, padding=6))
        x3 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=4, padding=8))
        
        # concatenating features
        out_feature = torch.cat((x1, x2, x3), 1)
        # print("After dilated conv,:", out_feature.shape)

        if block_num < 4:
            out_feature = self.row_pool(out_feature)
            # print("After row pooling:", out_feature.shape)

        # print("\nTop Branch:")
        top_branch_x = self.conv4_1x1_top(out_feature)
        # print("After 1x1 conv, shape:", top_branch_x.shape)
        top_branch_row_means = torch.mean(top_branch_x, dim=3)

        # print("Row means shape:", top_branch_row_means.shape)
        top_branch_proj_pools = top_branch_row_means.view(batch_size,self.block_conv1x1_output, height,1).repeat(1,1,1,top_branch_x.shape[3])
        # print("After projection pooling:", top_branch_proj_pools.shape)

        # print("\nBottom Branch:")
        bottom_branch_x = self.conv4_1x1_bottom(out_feature)
        # print("After 1x1 conv, shape:", bottom_branch_x.shape)
        bottom_branch_row_means = torch.mean(bottom_branch_x, dim=3)
        # print("Row means shape:", bottom_branch_row_means.shape)
        bottom_branch_proj_pools = bottom_branch_row_means.view(batch_size,1, height,1).repeat(1,1,1,top_branch_x.shape[3])
        # print("After projection pooling:", bottom_branch_proj_pools.shape)
        bottom_branch_sig_probs = torch.sigmoid(bottom_branch_proj_pools)
        # print("After sigmoid layer:", bottom_branch_sig_probs.shape)
        
        if block_num > 2:
            intermed_probs = bottom_branch_sig_probs[:,:,:,0]
            return (top_branch_proj_pools, 
                    bottom_branch_sig_probs, 
                    out_feature,
                    intermed_probs)

        return (top_branch_proj_pools, 
                bottom_branch_sig_probs,
                out_feature,
                None)

    def cpn_block(self, input_feature, block_num):
        height, width = input_feature.shape[-2:]

        # dilated convolutions 2/3/4
        x1 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=2, padding=4))
        x2 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=3, padding=6))
        x3 = F.relu(self.dil_conv2d(input_feature, self.block_inputs[block_num-1], dilation=4, padding=8))
        # x1.register_hook(lambda x: print(x))
        # concatenating features
        out_feature = torch.cat((x1, x2, x3), 1)

        if block_num < 4:
            out_feature = self.col_pool(out_feature)

        # print("\nTop Branch:")
        top_branch_x = self.conv4_1x1_top(out_feature)
        # print("After 1x1 conv, shape:", top_branch_x.shape)
        top_branch_row_means = torch.mean(top_branch_x, dim=2)

        # print("Row means shape:", top_branch_row_means.shape)
        top_branch_proj_pools = top_branch_row_means.view(batch_size,self.block_conv1x1_output,1,width).repeat(1,1,top_branch_x.shape[2],1)
        # print("After projection pooling:", top_branch_proj_pools.shape)

        # print("\nBottom Branch:")
        bottom_branch_x = self.conv4_1x1_bottom(out_feature)
        # print("After 1x1 conv, shape:", bottom_branch_x.shape)
        bottom_branch_row_means = torch.mean(bottom_branch_x, dim=2)
        # print("Row means shape:", bottom_branch_row_means.shape)
        bottom_branch_proj_pools = bottom_branch_row_means.view(batch_size,1,1,width).repeat(1,1,top_branch_x.shape[2],1)
        # print("After projection pooling:", bottom_branch_proj_pools.shape)
        bottom_branch_sig_probs = torch.sigmoid(bottom_branch_proj_pools)
        # print("After sigmoid layer:", bottom_branch_sig_probs.shape)
        
        if block_num > 2:
            intermed_probs = bottom_branch_sig_probs[:,:,1,:]
            return (top_branch_proj_pools, 
                    bottom_branch_sig_probs, 
                    out_feature,
                    intermed_probs)

        return (top_branch_proj_pools, 
                bottom_branch_sig_probs,
                out_feature,
                None)

    def forward(self, x):
        # print("Input shape:", x.shape)
        
        rpn_x = self.sfcn(x)
        cpn_x = rpn_x.clone().detach().requires_grad_(True)
        # cpn_x = self.sfcn(x)

        rpn_outputs = []
        cpn_outputs = []
        for block_num in range(self.blocks):
            # print("="*15,"BLOCK NUMBER:", block_num+1,"="*15)
            rpn_top, rpn_bottom, rpn_center, rpn_probs = self.rpn_block(input_feature=rpn_x, block_num=block_num+1)
            cpn_top, cpn_bottom, cpn_center, cpn_probs = self.cpn_block(input_feature=cpn_x, block_num=block_num+1)
            
            rpn_x = torch.cat((rpn_top, rpn_center, rpn_bottom), 1)
            cpn_x = torch.cat((cpn_top, cpn_center, cpn_bottom), 1)
            
            # print("RPN output shape:", rpn_x)
            # print("CPN output shape:", cpn_x)
            
            if rpn_probs is not None:
                rpn_outputs.append(rpn_probs)

            if cpn_probs is not None:
                cpn_outputs.append(cpn_probs)            

        return rpn_outputs, cpn_outputs

def get_logits(sig_probs):
    """
    Arguments:
    ----------
    sig_probs: output sigmoid probs from model
    """

    pos = sig_probs.squeeze(dim=0).view(batch_size,sig_probs.shape[2],1)
    neg = torch.sub(1, sig_probs.squeeze(dim=0)).view(batch_size,sig_probs.shape[2],1)
    logits = torch.cat((pos,neg),2)
    return logits

def cross_entropy_loss(logits, targets):
    """
    Arguments:
    ----------
    logits: (N, num_classes)
    targets: (N)
    """
    # print(logits.shape, targets.shape)
    # print(torch.abs(x-y))
    log_prob = -1.0 * F.log_softmax(logits, 1)
    # print(log_prob.shape)
    # print(targets.long().unsqueeze(2).shape)
    loss = log_prob.gather(2, targets.unsqueeze(2))
    loss = loss.mean()
    return loss

def splerge_loss(outputs, targets):
    """
    Arguments:
    ----------
    outputs: (rpn_outputs, cpn_outputs)
    targets: (rpn_targets, cpn_targets)
    """
    
    lambda3 = 0.1
    lambda4 = 0.25

    rpn_outputs, cpn_outputs = outputs
    rpn_targets, cpn_targets = targets

    r3, r4, r5 = rpn_outputs
    c3, c4, c5 = cpn_outputs
    
    # print(rpn_targets.shape)
    r3_logits = get_logits(r3)
    r4_logits = get_logits(r4)
    r5_logits = get_logits(r5)
    
    c3_logits = get_logits(c3)
    c4_logits = get_logits(c4)
    c5_logits = get_logits(c5)

    rl3 = cross_entropy_loss(r3_logits, rpn_targets)
    rl4 = cross_entropy_loss(r4_logits, rpn_targets)
    rl5 = cross_entropy_loss(r5_logits, rpn_targets)

    cl3 = cross_entropy_loss(c3_logits, cpn_targets)
    cl4 = cross_entropy_loss(c4_logits, cpn_targets)
    cl5 = cross_entropy_loss(c5_logits, cpn_targets)

    rpn_loss = rl5 + (lambda4 * rl4) + (lambda3 * rl3)
    cpn_loss = cl5 + (lambda4 * cl4) + (lambda3 * cl3)
    
    # print("rpn_loss:", round(rpn_loss.item(),4), "cpn_loss:", round(cpn_loss.item(),4))

    loss = rpn_loss + cpn_loss

    return loss

def collate_fn(batch):
    return tuple(zip(*batch))

def plot_grad_flow(named_parameters):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    '''Plots the gradients flowing through different layers in the net during training.
    Can be used for checking for possible gradient vanishing / exploding problems.
    
    Usage: Plug this function in Trainer class after loss.backwards() as 
    "plot_grad_flow(self.model.named_parameters())" to visualize the gradient flow'''
    ave_grads = []
    max_grads= []
    layers = []
    for n, p in named_parameters:
        print(p.grad)
        # p.grad = torch.Variable(10)
        # if(p.requires_grad) and ("bias" not in n):
        #     layers.append(n)
        #     ave_grads.append(p.grad.abs().mean())
        #     max_grads.append(p.grad.abs().max())
    plt.bar(np.arange(len(max_grads)), max_grads, alpha=0.1, lw=1, color="c")
    plt.bar(np.arange(len(max_grads)), ave_grads, alpha=0.1, lw=1, color="b")
    plt.hlines(0, 0, len(ave_grads)+1, lw=2, color="k" )
    plt.xticks(range(0,len(ave_grads), 1), layers, rotation="vertical")
    plt.xlim(left=0, right=len(ave_grads))
    plt.ylim(bottom = -0.001, top=0.02) # zoom in on the lower gradient regions
    plt.xlabel("Layers")
    plt.ylabel("average gradient")
    plt.title("Gradient flow")
    plt.grid(True)
    plt.legend([Line2D([0], [0], color="c", lw=4),
                Line2D([0], [0], color="b", lw=4),
                Line2D([0], [0], color="k", lw=4)], ['max-gradient', 'mean-gradient', 'zero-gradient'])

num_epochs = 50
batch_size = 2
learning_rate = 0.001

MODEL_STORE_PATH = 'model'

train_images_path = "data/images"
train_labels_path = "data/labels"

print("Loading dataset...")
dataset = TableDataset(os.getcwd(), train_images_path, train_labels_path, get_transform(train=True))

# split the dataset in train and test set
torch.manual_seed(1)
indices = torch.randperm(len(dataset)).tolist()

train_dataset = torch.utils.data.Subset(dataset, indices[:-20])
test_dataset = torch.utils.data.Subset(dataset, indices[-20:])

# define training and validation data loaders
train_loader = DataLoader(
    dataset=train_dataset, batch_size=batch_size, shuffle=True, num_workers=2)
   # collate_fn=collate_fn)

test_loader = DataLoader(dataset=test_dataset, batch_size=batch_size, shuffle=False)

device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

print("creating splerge model...")
model = Splerge().to(device)
# model = Splerge()
# print(model)

# print(dir(model))
print(model.cpn_block)
plot_grad_flow(model.named_parameters())
# for name, param in model.named_parameters():
#     print(name)
exit(0)

criterion = splerge_loss
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

# Train the model
total_step = len(train_loader)
loss_list = []
acc_list = []

from torchsummary import summary
summary(model, (3,100,100))

model.train()
print("starting training...")
for epoch in range(num_epochs):
    for i, (images, targets) in enumerate(train_loader):
        images = images.to(device)
        
        targets[0] = targets[0].long().to(device)
        targets[1] = targets[1].long().to(device)
        
        # print("images:", images.shape)
        # print("targets", targets[0].shape)
        
        # Run the forward pass
        outputs = model(images)
        loss = criterion(outputs, targets)

        loss_list.append(loss.item())

        # Backprop and perform Adam optimisation
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # # Track the accuracy
        # total = labels.size(0)
        # _, predicted = torch.max(outputs.data, 1)
        # correct = (predicted == labels).sum().item()
        # acc_list.append(correct / total)

        if (i + 1) % 5 == 0:
            print('Epoch [{}/{}], Step [{}/{}], Loss: {:.4f}'
                  .format(epoch + 1, num_epochs, i + 1, total_step, loss.item()))

"""
num_epochs = 1
num_classes = 10
batch_size = 1
learning_rate = 0.001

DATA_PATH = 'data'
MODEL_STORE_PATH = 'model'

# transforms to apply to the data
trans = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])

# MNIST dataset
train_dataset = torchvision.datasets.MNIST(root=DATA_PATH, train=True, transform=trans, download=True)
test_dataset = torchvision.datasets.MNIST(root=DATA_PATH, train=False, transform=trans)

train_loader = DataLoader(dataset=train_dataset, batch_size=batch_size, shuffle=True)
test_loader = DataLoader(dataset=test_dataset, batch_size=batch_size, shuffle=False)

model = RPN()
print(model)

# Loss and optimizer
criterion = torch.nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

# Train the model
total_step = len(train_loader)
loss_list = []
acc_list = []

for epoch in range(num_epochs):
    for i, (images, labels) in enumerate(train_loader):
        # Run the forward pass
        print(images.shape)
        outputs = model(images)
        print(outputs.shape)
        break
        loss = criterion(outputs, labels)
        loss_list.append(loss.item())

        # Backprop and perform Adam optimisation
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # Track the accuracy
        total = labels.size(0)
        _, predicted = torch.max(outputs.data, 1)
        correct = (predicted == labels).sum().item()
        acc_list.append(correct / total)

        if (i + 1) % 100 == 0:
            print('Epoch [{}/{}], Step [{}/{}], Loss: {:.4f}, Accuracy: {:.2f}%'
                  .format(epoch + 1, num_epochs, i + 1, total_step, loss.item(),
                          (correct / total) * 100))

"""