from pathlib import Path

import pytorch_lightning as pl
import torch
import wandb

try:
    import torchviz
    no_torchviz = False
except ImportError:
    no_torchviz = True


class LoggedLitModule(pl.LightningModule):
    """LightningModule plus wandb features and simple training/val steps.
    Assumes that your training loop involves inputs (xs)
    producing outputs (y_hats)
    that are compared to targets (ys)
    by a loss and by metrics,
    where each batch == (xs, ys).
    """

    def __init__(self, max_logged_inputs=0):
        super().__init__()

        self.training_metrics = []
        self.validation_metrics = []

        self.max_logged_inputs = max_logged_inputs
        self.graph_logged = False

    def on_pretrain_routine_start(self):
        print(self)
        print(f"Parameter Count: {self.count_params()}")

    def training_step(self, xys, idx):
        xs, ys = xys
        y_hats = self.forward(xs)
        loss = self.loss(y_hats, ys)

        logging_scalars = {"loss": loss}
        for metric in self.training_metrics:
            metric_str = metric.__class__.__name__.lower()
            logging_scalars[metric_str] = metric(y_hats, ys)

        self.do_logging(xs, ys, idx, y_hats, logging_scalars)

        return loss

    def validation_step(self, xys, idx):
        xs, ys = xys
        y_hats = self.forward(xs)
        loss = self.loss(y_hats, ys)

        logging_scalars = {"loss": loss}
        for metric in self.validation_metrics:
            metric_str = metric.__class__.__name__.lower()
            logging_scalars[metric_str] = metric(y_hats, ys)

        self.do_logging(xs, ys, idx, y_hats, logging_scalars, step="validation")

    def do_logging(self, xs, ys, idx, y_hats, scalars, step="training"):
        self.log_dict(
            {step + "/" + name: value for name, value in scalars.items()})

        if idx == 0:
            if "x_range" not in wandb.run.config.keys():
                wandb.run.config["x_range"] = [float(torch.min(xs)), float(torch.max(xs))]
            if "loss" not in wandb.run.config.keys():
                wandb.run.config["loss"] = self.detect_loss()
            if "optimizer" not in wandb.run.config.keys():
                wandb.run.config["optimizer"] = self.detect_optimizer()
            if "nparams" not in wandb.run.config.keys():
                wandb.run.config["nparams"] = self.count_params()
            if "dropout" not in wandb.run.config.keys():
                wandb.run.config["dropout"] = self.detect_dropout()
            if step == "training":
                if self.max_logged_inputs > 0:
                    self.log_examples(xs, ys, y_hats)
                if not (self.graph_logged or no_torchviz):
                    self.log_graph(y_hats)

    def detect_loss(self):
        classname = self.loss.__class__.__name__
        if classname in ["method", "function"]:
            return "unknown"
        else:
            return classname

    def detect_optimizer(self):
        return self.optimizers().__class__.__name__

    def count_params(self):
        return sum(p.numel() for p in self.parameters())

    def detect_dropout(self):
        for module in self.modules():
            if isinstance(module, torch.nn.Dropout):
                return module.p
        return 0

    def log_graph(self, y_hats):
        params_dict = dict(list(self.named_parameters()))
        graph = torchviz.make_dot(y_hats, params=params_dict)
        graph.format = "png"
        fname = Path(self.logger.experiment.dir) / "graph"
        graph.render(fname)
        wandb.save(str(fname.with_suffix("." + graph.format)))
        self.graph_logged = True

    def log_examples(*args, **kwargs):
        raise NotImplementedError


class LoggedImageClassifierModule(LoggedLitModule):

    def __init__(self, max_images_to_display=32, labels=None):

        super().__init__(max_logged_inputs=max_images_to_display)

        self.train_acc = pl.metrics.Accuracy()
        self.valid_acc = pl.metrics.Accuracy()

        self.training_metrics.append(self.train_acc)
        self.validation_metrics.append(self.valid_acc)

        self.labels = labels

    def log_examples(self, xs, ys, y_hats):
        xs, ys, y_hats = (xs[:self.max_logged_inputs],
                          ys[:self.max_logged_inputs],
                          y_hats[:self.max_logged_inputs])

        if y_hats.shape[-1] == 1:  # handle single-class case
            preds = torch.greater(y_hats, 0.5)
            preds = [bool(pred) for pred in preds]
        else:  # assume we are in the typical one-hot case
            preds = torch.argmax(y_hats, 1)

        if self.labels is not None:
            preds = [self.labels[int(pred)] for pred in preds]

        images_with_predictions = [
            wandb.Image(x, caption=f"Pred: {pred}")
            for x, pred in zip(xs, preds)]

        self.logger.experiment.log({"predictions": images_with_predictions,
                                    "global_step": self.global_step}, commit=False)
